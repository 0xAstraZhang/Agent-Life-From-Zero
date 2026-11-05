# 第 18 章：一次只增加一种运行能力

上一章：[沙箱](17-sandbox.md) · [目录](README.md) · [完整参数参考](18-runtime-reference.md)

Agent 已经能做任务。本章从最小的“记录一次请求用了多久”开始，再分别加入重试、任务期限、上下文压缩和进程日志。每个检查点独立运行，不需要一次读懂所有运行参数。

```bash
uv run python scripts/check_tutorial.py --chapter 18 --export /tmp/minicode-ch18
cd /tmp/minicode-ch18
uv sync
uv run python examples/18_runtime.py --step minimal
```

真实 API 配置沿用[第 00 章](00-setup.md)。完整练习文件是 [lessons/ch18.py](../lessons/ch18.py)。先逐项实现下面的变化，再与最终模块比较；模型参数、费用字段与平台限制保留在[参数参考](18-runtime-reference.md)。

## 1. 最小检查点：只记录一次请求

先给模型套一个小包装器。它不改变请求和回复，只在前后读取时钟：

```python
from dataclasses import asdict
import time


class TimedModel:
    def __init__(self, inner, record):
        self.inner, self.record = inner, record
        self.count = 0

    def complete(self, messages, tools=()):
        started = time.monotonic()
        reply = self.inner.complete(messages, tools)
        self.count += 1
        self.record({"type": "request_end", "local_request_id": f"lesson-{self.count}",
                     "seconds": time.monotonic() - started,
                     "normalized_usage": asdict(reply.usage),
                     "usage": getattr(reply, "usage_details", {})})
        return reply
```

在完整练习文件中，`record` 先只是列表的 `append`：

```python
records = []
reply = TimedModel(model, records.append).complete([Message.text("user", "你好，用一句话回复。")])
print(message_text(reply.message))
print(json.dumps(records, ensure_ascii=False))
```

运行后看两样东西：真实回复，以及包含 `seconds`、输入/输出 token 的记录。先确认原回复没有被包装器改变，再考虑失败请求怎样记录。

## 2. 单独增加一次连接重试

```bash
uv run python examples/18_runtime.py --step retry
```

这个检查点只处理一种情况：连接尚未建立，等待一下，再尝试一次。它既不重试工具，也不处理流式输出：

```python
import httpx
import time


class RetryBeforeSend:
    def __init__(self, inner, delay=0.1):
        self.inner, self.delay, self.retries = inner, delay, 0

    def complete(self, messages, tools=()):
        try:
            return self.inner.complete(messages, tools)
        except httpx.ConnectError:
            self.retries += 1
            time.sleep(self.delay)
            return self.inner.complete(messages, tools)
```

真实连接正常时，重试次数是 0。用显式离线注入观察失败路径：

```bash
uv run python examples/18_runtime.py --step retry --offline
```

这次第一次调用抛出连接错误，第二次返回回复，重试次数应为 1。实验关闭内层适配器的自动重试，避免两层重试混在一起。

之后再读 [providers/base.py](../minicode/providers/base.py) 中的完整实现，依次增加可恢复状态码、指数退避、重试上限和日志。认证错误需要修改配置；读超时可能已经生成内容；已经输出的流不能直接从头重放。这些情况需要各自的测试。

## 3. 给整个任务一个期限

```bash
uv run python examples/18_runtime.py --step deadline
```

这个检查点让模型调用固定的 Wait 工具（等待 0.05 秒），再回答完成。先只在原 Agent 创建处增加一个参数：

```python
agent = Agent(model, runner, max_turns=4, task_timeout=60)
```

真实 API 默认给任务 60 秒，正常完成应看到 `success`。然后用一个确定的离线实验观察期限真的生效：

```bash
uv run python examples/18_runtime.py --step deadline --offline
```

这里工具仍然真实等待 0.05 秒，期限缩短为 0.01 秒。预期终止状态是 `timeout`，检查点将这个预期结果记为通过；只回复“完成”或工具没有执行都不通过。通过 `--task-timeout` 可以调整期限；例如真实 API 下设置较短期限，观察请求阶段是否已经耗尽时间。

实现时在任务开始计算 `deadline = time.monotonic() + seconds`，每次请求与工具执行都使用剩余时间；不能每进入一轮就重新获得 60 秒。

最小的边界检查是：

```python
remaining = deadline - time.monotonic()
if remaining <= 0:
    raise TimeoutError("任务已达到时间上限")
```

完整实现还把剩余时间传给 HTTP 和进程工具。在支持 `setitimer` 的 POSIX 主线程中可以中断阻塞调用；其他运行环境依靠下层超时和工具配合。先理解“同一个绝对截止时间”，再读平台分支。

## 4. 对长任务只压缩一次请求视图

```bash
uv run python examples/18_runtime.py --step context
```

实验只有一条用户目标“找出 answer 的值”，后面连续完成四次工具往返，不添加第二条用户输入。先摘要较早的工具往返，再带着原目标和近期结果继续请求。历史数据是练习输入；摘要和最终回复默认都由真实 API 生成。

```python
wrapped = CompactingModel(model, threshold_chars=300, keep_turns=1)
reply = wrapped.complete(history)
print("压缩次数：", wrapped.compactions)
print("完整历史条数：", len(history))
```

预期压缩一次，原历史条数不变。第 10 章在新的用户轮开始时摘要，本次则在同一个任务的工具往返之间切分。继续保留两份数据：完整历史保留事实，请求视图是本次发给模型的材料。

随后再扩展[compact.py](../minicode/compact.py)：只在完整工具往返之后切分；视图再次超限时，用旧摘要和新移出的内容继续摘要；视图未超限时复用已有摘要。最后才加入 token 估算与输出空间预留。不要每轮都重新摘要整段历史。

## 5. 单独观察进程和日志分页

```bash
uv run python examples/18_runtime.py --step processes
```

这个检查点不请求模型。它只启动仓库内写明的 Python 命令，输出两行，然后分别读取第一页与第二页：

```python
import shlex
import sys

from minicode.processes import ProcessManager

command = shlex.join([sys.executable, "-c", "print('first'); print('second')"])
with ProcessManager() as manager:
    session = manager.start(command, context, timeout=5, interactive=False)
    first = manager.poll(session.id, context, wait=5, limit=6)
    second = manager.read_log(session.id, context, offset=6, limit=20)
    print(first.content)
    print(second.content)
```

这段代码放在已有 `ToolContext` 的任务中，完整练习会创建临时工作目录。第一页是 `first`，第二页是 `second`；验收同时检查两页均读取成功。日志里的 `next_offset` 说明下一页从哪里继续；退出 `with` 时回收进程和临时日志。

理解这个最小生命周期后，再接入 BashStart、BashPoll、BashLog，然后增加输入与停止操作。日志分页按字节计算；Read 的 `offset` 则按行计算。后台会话使用管道，不是 PTY；恢复对话也不会恢复已退出的进程。

## 6. 增强检查点：把一次任务记录为 JSONL

```bash
uv run python examples/18_runtime.py --step enhanced
```

现在把最开始的列表换成 [RunRecorder](../minicode/observability.py)。它接收模型和 Agent 的事件，写入同一条时间线：

```python
recorder = RunRecorder(root / "run.jsonl")
try:
    model.observer = recorder.record
    agent = Agent(model, runner, observer=recorder.record)
    for event in agent.run("读取 answer.py 的第二行，再回答 answer 的值"):
        print_event(event)
    print(recorder.summary())
finally:
    recorder.close()
```

完整练习在临时目录运行，显示工具结果、最终回答、记录摘要和记录条数；退出后清理临时目录。离线模型没有请求观察器，因此离线检查点使用第一步的 `TimedModel` 写入确定记录。

需要保存真实任务的记录时，用最终 CLI 的 `--event-log PATH`，选择一个新文件。运行记录保存计量元数据；`--session` 另存对话历史。

## 7. 需要时再打开参数参考

前面的检查点各自只增加一种机制。以下内容按实际需要查询，不作为这些实验的前置步骤：

| 需要解决的问题 | 参考内容 |
| --- | --- |
| 固定模型的思考与输出行为 | thinking、reasoning effort、tool choice、输出格式与协议兼容性 |
| 为任务定上限 | 请求超时、任务期限、轮数、token 预算 |
| 同时执行独立读取 | parallel_tools 与稳定的结果顺序 |
| 计算费用 | 显式价表、缓存分项、未知 usage 的处理 |
| 长期使用后台服务 | BashStart/Poll/Send/Stop/Log 的生命周期与权限 |

这些说明完整保存在[运行控制与记录参考](18-runtime-reference.md)。默认参数值、支持范围和价格都应以选定服务为准。完成一次真实任务并保存验收结果后，再比较参数变化是否改善任务效果。

## 验收

```bash
uv run python examples/18_runtime.py --step minimal --offline
uv run python examples/18_runtime.py --step retry --offline
uv run python examples/18_runtime.py --step deadline --offline
uv run python examples/18_runtime.py --step context --offline
uv run python examples/18_runtime.py --step processes
uv run python examples/18_runtime.py --step enhanced --offline
uv run python -m pytest tests/test_lesson18.py -q
```

然后运行本阶段累计测试。原教程仓库还运行 `scripts/check_tutorial.py`、`python -m minicode.evals` 与 `uv build`，确认阶段导出、任务验收和安装包仍可使用。
