# 第 03 章：把已经能运行的原型提取成协议适配器

上一章：[工具注册表](02-tools.md) · [目录](README.md) · 下一章：[Responses 扩展](04-responses.md)

你已经用真实 API 跑通了 Agent。这一章保留相同的请求、工具执行和历史回传，把服务商字段集中到适配器里。后面增加工具或换协议时，循环就不用一起改。

## 1. 本章要保留的行为

还是计算 `2 + 3`。提取前后都应该观察到：

```text
用户问题 → 助手调用 add → 本地结果 5 → 助手最终回答
```

先在仓库根目录导出本章，获得当前阶段的完整文件：

```bash
uv run python scripts/check_tutorial.py --chapter 3 --export /tmp/minicode-ch03
cd /tmp/minicode-ch03
uv sync
```

目标须为空。导出目录只包含已经引入的功能；仓库根目录的 `minicode/` 是最终版本。以下命令在本章目录执行，API 配置继续沿用第 00 章。

## 2. 先划出三个职责

第二章的字典直接使用了 `tool_calls`、`function.arguments`、`finish_reason`。这些都是 Chat Completions 的字段。我们先给程序内部的同一件事起固定名称：

| 现在的内容 | 内部表示 | 谁负责转换 |
| --- | --- | --- |
| `role` 和文本 | `Message`、`Text` | 模型适配器 |
| 工具名称、参数、调用 ID | `ToolCall` | 模型适配器 |
| 本地函数返回值 | `ToolResult` | 工具执行器 |
| 工具名称、说明、参数格式 | `ToolSpec` | 适配器包装成协议字段 |
| 一轮完整回复和停止原因 | `ModelReply` | 模型适配器 |

循环只需要两条接口：

```text
model.complete(messages, tool_specs) → ModelReply
runner.run(tool_call)                → ToolResult
```

工具函数仍在本地执行。适配器只做“发请求、读回复、转换字段”，不执行工具。

按下面的顺序建立文件。先对照正常消息和工具往返，增强字段在后文再解释：

| 第二章中的内容 | 本章完整文件 |
| --- | --- |
| 消息字典和工具声明 | [types.py](../lessons/stages/03/minicode/types.py) |
| 工具注册表、分发和校验 | [tool.py](../lessons/stages/03/minicode/tool.py) |
| `run()` 循环与消息历史 | [agent.py](../lessons/stages/03/minicode/agent.py) |
| 模型所需的 `complete()` 能力 | [model.py](../lessons/stages/03/minicode/model.py) |
| HTTP 连接和配置 | [providers/base.py](../lessons/stages/03/minicode/providers/base.py) |
| Chat 字段转换 | [providers/chat.py](../lessons/stages/03/minicode/providers/chat.py) |
| 创建模型客户端的入口 | [providers/__init__.py](../lessons/stages/03/minicode/providers/__init__.py) |

第 00–02 章快照保持原样。正式模块使用自己的公共类型，不再导入早期的 `lessons.ch01` 或 `lessons.ch02`。

## 3. 最小原型：显式完成两次模型请求

先运行：

```bash
uv run python examples/03_chat_completions.py --step minimal
```

这一步直接写出两次请求，暂时不借助 `Agent` 类。完整入口是 [03_chat_completions.py](../examples/03_chat_completions.py)。下面是可复制的检查点：

```python
# 可运行检查点：03-minimal
from pathlib import Path
from lessons.runtime import open_model, parse_args
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, ToolSpec, message_text, tool_calls

args = parse_args("正式适配器的最小往返", [])
spec = ToolSpec("add", "两个整数相加", {
    "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"], "additionalProperties": False,
})
runner = ToolRunner([Tool(spec, lambda args, context: str(args["a"] + args["b"]))], ToolContext(Path.cwd()))
with open_model(args) as model:
    messages = [Message.text("user", "请调用 add 工具计算 2 加 3，再回答。")]
    first = model.complete(messages, runner.specs())
    if first.stop_reason != "tool_calls" or not tool_calls(first.message):
        raise RuntimeError("本次练习需要一个完整的工具调用")
    messages.append(first.message)
    for request in tool_calls(first.message):
        result = runner.run(request)
        if result.is_error:
            raise RuntimeError(result.content)
        messages.append(Message("tool", [result]))
    second = model.complete(messages, runner.specs())
    if second.stop_reason != "stop":
        raise RuntimeError("模型还没有给出最终回答")
    print(message_text(second.message))
```

和第一章相比，历史的顺序不变，只把协议字典换成了内部对象。`ToolContext` 暂时只保存工作目录；计算工具不使用它，第 06 章读写文件时会用到。

`open_model()` 负责读取同一组环境变量、创建和关闭客户端。它默认请求真实 API，完整代码在 [lessons/runtime.py](../lessons/runtime.py)。

## 4. 沿一个调用理解编码与解码

模型返回的参数是字符串，程序内部使用字典：

```text
HTTP function.arguments = "{\"a\":2,\"b\":3}"
                 ↓ 解码
ToolCall(id="call_1", name="add", arguments={"a": 2, "b": 3})
                 ↓ 执行
ToolResult(call_id="call_1", content="5")
                 ↓ 编码
HTTP {"role":"tool", "tool_call_id":"call_1", "content":"5"}
```

在 `ChatCompletionsModel` 中沿这条线读两个方法：

1. `decode()` 从 `choices[0].message` 取得文本和工具调用，转换成内部内容块。
2. `encode()` 把整个内部历史转回 HTTP 消息，同时把 `ToolSpec` 包装成 `tools` 声明。

先跟踪一个正常调用，确认第二次请求仍包含原始助手调用和匹配 ID 的工具结果。不要从异常分支开始读完整文件。

## 5. 增强一步：再交回 Agent 循环

刚才固定写了两次请求，适合观察数据；模型如果还需要第二个工具，就要继续循环。现在使用从第二章提取出的 `Agent`：

```python
# 可运行检查点：03-enhanced
from pathlib import Path
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec

args = parse_args("正式 Agent 循环", [])
spec = ToolSpec("add", "两个整数相加", {
    "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"], "additionalProperties": False,
})
runner = ToolRunner([Tool(spec, lambda args, context: str(args["a"] + args["b"]))], ToolContext(Path.cwd()))
with open_model(args) as model:
    events = run_events(Agent(model, runner, max_turns=4).run("请调用 add 计算 2 加 3，再回答。"))
```

```bash
uv run python examples/03_chat_completions.py --step enhanced
```

`run_events()` 显示事件，并在任务未成功结束时报告失败。上面的片段用来观察数据流；完整入口还检查本地确实执行了 `add(2, 3)` 并得到 `5`，避免把模型直接说出答案当成工具实验通过。

这时再理解正式类型里增加的细节：

| 已经遇到的问题 | 对应补充 |
| --- | --- |
| 真实请求消耗了多少 token | `Usage`，每轮累加并随结果报告 |
| 参数不是合法 JSON | `raw_arguments`、`parse_error`，执行器把错误回传 |
| 输出被截断或拒绝 | 停止原因转换；未完成调用不执行 |
| 兼容服务要求回传额外助手字段 | `provider_data` 保存同来源续轮数据 |

`provider_data` 不由 Agent 解释。适配器在来源一致、内部消息未修改时回放；这属于协议兼容细节，不改变循环的骨架。

配置里的 `api` 选择协议，`provider` 标记服务来源。当前使用 `openai-chat`。输出上限字段的兼容差异由适配器处理：例如 `max_tokens` 与 `max_completion_tokens`，不要把服务商条件放进 Agent 循环。

## 6. 验收：正式重构是否保持了行为

```bash
uv run python examples/03_chat_completions.py --offline --step minimal
uv run python examples/03_chat_completions.py --offline --step enhanced
uv run python -m pytest tests/test_ch03.py -q
```

离线示例固定模型的决定，便于比较重构前后行为。协议测试另外通过 `httpx.MockTransport` 检查实际 HTTP 请求体，尤其是第二次请求是否带上调用 ID 和结果。两种测试分别验证循环与协议映射。

练习：先把正常 HTTP 回复改成截断，确认工具不执行；再单独改坏参数 JSON，确认错误回传。一次只改变一个条件。

另有 [live_model.py](../examples/live_model.py) 可以检查真实加法调用是否发生：

```bash
uv run python examples/live_model.py --api openai-chat --env-prefix MINICODE
```

已有 `LLM_*` 配置则改为 `--env-prefix LLM`。

接下来可以直接学习[第 06 章：编码工具](06-coding-tools.md)，把 Agent 用在真实文件上。第 04、05 章是更换协议的扩展阅读：需要 Responses 或 Anthropic 时，再比较同一条工具往返怎样映射到另一种 HTTP 格式。

协议参考：[Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[Function calling](https://developers.openai.com/api/docs/guides/function-calling)。
