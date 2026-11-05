# 第 18 章：亲手加入计时、重试和共同期限

上一章：[沙箱](17-sandbox.md) · [目录](README.md) · [仓库最终实现参考](18-runtime-reference.md)

继续在自己的 `my-agent/` 编程。本章只实现三件事：记录一次请求的耗时；连接尚未建立时重试一次；让模型与工具共用一个任务期限。先运行计时版，再把后两项加到已有 Agent 上。

这里使用前面亲手写的 `model.open_model()`、`agent.Agent` 和 `tools.execute_call()`。不会导入仓库的运行控制大类。

## 1. 最小原型：计时包装器

新建 `timed_model.py`：

```python file=timed_model.py step=minimal
import time


class TimedModel:
    def __init__(self, inner, records):
        self.inner = inner
        self.records = records

    def complete(self, messages, tools=()):
        started = time.monotonic()
        reply = self.inner.complete(messages, tools)
        self.records.append({
            "seconds": time.monotonic() - started,
            "finish_reason": reply["finish_reason"],
        })
        return reply
```

读这段时只抓住三个动作：记下开始时间，调用原模型，记录耗时后返回原回复。它不改变消息，也不替 Agent 决定是否执行工具。

## 2. 用真实 API 运行计时版

替换 `demo.py`：

```python file=demo.py step=minimal
import json

from model import open_model
from timed_model import TimedModel

records = []
with open_model() as model:
    reply = TimedModel(model, records).complete([
        {"role": "user", "content": "你好，请用一句话回复。"},
    ])
message = reply["message"]
if reply["finish_reason"] != "stop" or message.get("tool_calls") or not message.get("content"):
    raise RuntimeError("没有得到完整的文本回复")
print(message["content"])
print(json.dumps(records, ensure_ascii=False))
print("请求记录数：", len(records))
```

运行 `uv run python demo.py`。应看到模型回复、一次耗时和 `请求记录数： 1`。这里只记录成功返回的请求；失败记录可以在后续增加 `try/finally` 后再验证。

## 3. 增强的第一步：只重试连接错误

连接还没建立时，等待一下再试一次。先不处理读超时、认证失败或流式中断。

新建 `request_limits.py`，先写入下面的完整文件。除了重试类，还把下一步需要的共同期限放在同一文件中：

```python file=request_limits.py step=enhanced
import time

import httpx


class RetryBeforeSend:
    def __init__(self, inner, delay=0.1):
        self.inner = inner
        self.delay = delay
        self.retries = 0

    def complete(self, messages, tools=()):
        try:
            return self.inner.complete(messages, tools)
        except httpx.ConnectError:
            self.retries += 1
            time.sleep(self.delay)
            return self.inner.complete(messages, tools)


class Deadline:
    def __init__(self, seconds):
        if seconds <= 0:
            raise ValueError("任务期限必须大于零")
        self.end = time.monotonic() + seconds

    def check(self):
        if time.monotonic() >= self.end:
            raise TimeoutError("任务期限已经耗尽")


class DeadlineModel:
    def __init__(self, inner, deadline):
        self.inner = inner
        self.deadline = deadline

    def complete(self, messages, tools=()):
        self.deadline.check()
        reply = self.inner.complete(messages, tools)
        self.deadline.check()
        return reply
```

先单独读 `RetryBeforeSend`。第二次调用在 `except` 内直接返回，没有第三次重试。我们只捕获 `ConnectError`，其他错误继续交给调用方。

然后读 `Deadline`：任务开始时只计算一次结束时间。每次检查都与同一个时间比较，不在下一轮重新获得一份时长。

`DeadlineModel` 在模型请求前后检查期限。这是执行边界上的检查，还不能立即打断任意阻塞请求；请求进行中的等待仍受 HTTP 客户端自身的超时控制。

## 4. 让工具使用同一个期限

新建 `limited_task.py`：

```python file=limited_task.py step=enhanced
import time

from agent import Agent
from request_limits import Deadline, DeadlineModel, RetryBeforeSend
from timed_model import TimedModel
from tools import execute_call


def run_limited_task(model, seconds=60):
    records = []
    waited = []
    deadline = Deadline(seconds)
    retrying = RetryBeforeSend(model)
    timed = TimedModel(retrying, records)
    limited = DeadlineModel(timed, deadline)

    def wait():
        time.sleep(0.02)
        waited.append(True)
        return "等待完成"

    tools = {"Wait": {
        "spec": {"type": "function", "function": {
            "name": "Wait", "description": "等待 0.02 秒后返回。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }},
        "execute": wait,
        "read_only": True,
    }}

    def run_tool(call):
        deadline.check()
        result = execute_call(call, tools)
        deadline.check()
        return result

    answer = Agent(limited, tools, runner=run_tool, max_turns=4).run(
        "必须调用 Wait 工具一次，看到结果后再回答完成。"
    )
    print(answer)
    print("Wait 实际执行：", bool(waited))
    if not waited:
        raise RuntimeError("模型没有实际执行 Wait")

    # 把同一个期限移到过去，模拟任务已经耗尽时间。
    deadline.end = time.monotonic() - 1
    before = len(records)
    blocked = False
    try:
        limited.complete([{"role": "user", "content": "这次请求不应发出。"}])
    except TimeoutError:
        blocked = True
    print("过期后阻止请求：", blocked and len(records) == before)
    print("连接重试次数：", retrying.retries)
    print("逻辑请求记录数：", len(records))
    if not blocked or len(records) != before:
        raise RuntimeError("过期任务仍然发出了模型请求")
    return records
```

现在数据流是：

```text
Agent → 期限检查 → 计时 → 连接重试 → 真实模型
Agent → 同一个期限检查 → 工具执行 → 同一个期限检查
```

正常任务先执行真实 Wait，再取得模型回答。随后我们主动把同一个期限设到过去，检查下一次请求在发出前就被阻止。这个实验不靠恰好慢一点的网络来触发超时。

## 5. 运行增强版

替换 `demo.py`：

```python file=demo.py step=enhanced
from limited_task import run_limited_task
from model import open_model

with open_model() as model:
    run_limited_task(model)
```

运行 `uv run python demo.py`。预期 `Wait 实际执行` 和 `过期后阻止请求` 都为 `True`。正常的一次工具往返产生两条逻辑请求记录；网络正常时重试次数为 0。

测试重试分支时，在 HTTP 层注入一次 `httpx.ConnectError`，后续返回正常响应。这样重试次数应为 1，工具仍只执行一次。不要为了测试而把工具重复执行一遍。

## 6. 先明确手写版的范围

我们已经实现了非流式请求计时、一次连接重试，以及模型和工具共用的期限检查。还有几个限制可以通过下一轮实验逐项处理：

- 计时器当前记录成功的逻辑请求，不是每次 HTTP 尝试。
- 重试等待本身还没按剩余期限缩短。期限耗尽后会在外层检查处停止。
- 已发出的模型请求和正在执行的普通函数不会被这几行代码强制取消。
- 读取超时可能发生在服务端已经生成内容之后，不能直接照搬连接错误的处理。
- 本章没有实现 token 预算、费用换算、增量压缩或后台进程管理。

完整的模型参数、运行记录、压缩和进程功能保留在[仓库最终实现参考](18-runtime-reference.md)。这些是后续可以选择的扩展，不属于本章已经手写完成的程序。

练习：先让 `TimedModel` 在失败时也写一条记录；再给 `RetryBeforeSend` 传入共同的 Deadline，使它在等待和再次请求前检查剩余时间。每次改动都先说明它解决了哪一种实际失败。
