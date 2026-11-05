# 第 8 章：流式输出、完整回复与中断

先回到教程仓库根目录，执行下面的导出命令；随后进入独立的本章目录。手写时保留自己的练习目录，用导出版本核对文件清单和差异：

```bash
uv run python scripts/check_tutorial.py --chapter 08 --export /tmp/minicode-ch08
cd /tmp/minicode-ch08
uv sync
```

以下运行和测试命令在本章练习目录中执行。目录只含第 00–08 章的代码与测试；文件增量由[阶段清单](../lessons/stages.json)记录。导出目标必须为空，不会覆盖已有练习。

[上一章：权限](07-permissions.md) · [目录](README.md) · [下一章：上下文](09-context.md)

前面每次请求都等待完整响应。工具执行可能只有几毫秒，模型却要生成几秒钟，用户这段时间什么都看不到。流式输出把文本到达的过程显示出来。

这一章要守住一个边界：增量是界面事件，完整回复才可以进入历史和触发工具。收到半段 JSON 不等于模型已经决定执行。

## 本章完整变更

第 07 章结束时只有 `complete()`。这一章才把流式接口加入模型、适配器和 Agent，按下表替换完整文件：

| 文件 | 本章新增内容 |
| --- | --- |
| [types.py](../lessons/stages/08/minicode/types.py) | 在已有类型后增加 `StreamEvent`，仍没有第 10 章序列化函数 |
| [model.py](../minicode/model.py) | 模型协议增加 `stream()` |
| [testing.py](../minicode/testing.py) | FakeModel 可以逐字产出文本，再发送完成事件 |
| [providers/base.py](../lessons/stages/08/minicode/providers/base.py) | SSE 分帧与 HTTP 流生命周期 |
| [chat.py](../lessons/stages/08/minicode/providers/chat.py)、[responses.py](../lessons/stages/08/minicode/providers/responses.py)、[anthropic.py](../lessons/stages/08/minicode/providers/anthropic.py) | 各自协议的流状态机 |
| [providers/demo.py](../minicode/providers/demo.py) | 加入三个协议的 SSE 测试数据 |
| [agent.py](../lessons/stages/08/minicode/agent.py) | 增加流式请求、中断补记；仍没有第 10 章 `history` 参数 |
| [本章实验](../examples/08_streaming.py)、[本章测试](../tests/test_streaming.py) | 逐协议运行流式工具往返，验证断流不执行 |

表中链接与本章导出文件一致。模型和适配器到本章已经支持流式处理；第 18 章才继续增加控制参数和请求记录。Agent 和 types 也使用本章阶段版本。下面标为“完整替换”的代码可以直接写入对应位置，其余协议事件示例用于解释状态机。

前面章节只调用 `complete()`。在这里才增加 `stream=True` 分支，权限和工具执行仍使用已有包装器。

## 第一步：把 SSE 与模型语义分开

SSE 使用空行划分事件，一条事件可以有多行 `data:`，也可以夹杂注释心跳：

```text
: heartbeat

data: {"type":"response.output_text.delta","delta":"你好"}

```

`httpx` 负责 HTTP 字节流，`sse_data()` 负责按行组合 SSE 帧，协议适配器才解释 JSON 中的字段。

不要假定“网络读取一次”就等于“一个事件”。也不要把连接关闭直接当成成功：网络错误和正常 EOF 都可能发生在回复生成一半时。

## 第二步：定义统一事件

在第 03 章建立的 `minicode/types.py` 末尾增加下面这个完整类型定义：

```python
@dataclass
class StreamEvent:
    type: str
    text: str = ""
    call_id: str = ""
    arguments_delta: str = ""
    reply: ModelReply | None = None
```

本教程只需要三种类型：

| 事件 | 谁消费 | 能否触发工具 |
| --- | --- | --- |
| text_delta | 终端显示文本 | 否 |
| tool_call_delta | 调试界面观察参数片段 | 否 |
| done + ModelReply | Agent 检查结束原因、记录历史 | 只有完整且正常的工具回复才可以 |

`done` 指模型这一轮结束，不一定表示任务成功。它的 `reply.stop_reason` 仍可能是 length、refusal 或 error。

接着用下面的完整内容替换 `minicode/model.py`：

```python
"""模型边界：适配器处理协议，Agent 处理循环。"""
from collections.abc import Iterator
from typing import Protocol

from .types import Message, ModelReply, StreamEvent, ToolSpec


class ModelClient(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolSpec] = ()) -> ModelReply: ...

    def stream(self, messages: list[Message], tools: list[ToolSpec] = ()) -> Iterator[StreamEvent]: ...
```

在 `testing.py` 的类型导入中加入 `StreamEvent` 与 `message_text`，保留原来的 `complete()`，给 `FakeModel` 新增以下完整方法：

```python
    def stream(self, messages, tools=()):
        reply = self.complete(messages, tools)
        for char in message_text(reply.message):
            yield StreamEvent("text_delta", text=char)
        yield StreamEvent("done", reply=reply)
```

这样测试模型先具备了同样的流接口。`done` 中仍携带完整回复，Agent 不需要从显示用字符重新猜测最终消息。


## 第三步：分别重建三种协议的完整回复

### Chat Completions

每个 chunk 的 `delta` 可能包含文字，也可能包含一个或多个工具片段。工具按 `index` 聚合，不能把两个调用的参数拼到一起。

```text
index=0  arguments='{"a":'
index=1  arguments='{"a":4,'
index=0  arguments='2,"b":3}'
index=1  arguments='"b":5}'
```

得到两个独立调用后，还要等到 finish_reason 和 `[DONE]`。usage 可能在最后一个空 choices chunk 才到，因此不能见到 finish_reason 就提前停止读取。

最终把聚合后的响应交回已有 `decode()`。这样流式和非流式共用停止原因、参数解析和原始数据保留规则。

### Responses

文字来自 `response.output_text.delta`，工具参数来自 `response.function_call_arguments.delta`。参数增量引用的是 item ID；`response.output_item.added` 提供它与 call ID 的对应。

本实现把增量用于显示和调试，最终使用 `response.completed` 或其他终止事件里的完整 response 作为权威结果。这样 reasoning items、真实 call ID 和完整参数都通过同一个 `decode()` 处理。

如果流没有出现终止事件，就抛 `ProtocolError`。不能用已经收到的参数片段自己拼出一个“成功回复”。

### Anthropic Messages

状态机按下面的顺序处理：

```text
message_start
  content_block_start
  content_block_delta × N
  content_block_stop
  ...其他内容块...
message_delta
message_stop
```

文字块拼 `text_delta`；工具块拼 `input_json_delta.partial_json`；思考块分别拼 thinking 与 signature。

工具参数先按内容块收集，到 `message_stop` 再解析，此时已经知道停止原因和累计用量。若半截 JSON 是因 `max_tokens` 截断，仍返回 `length` 并保留用量；正常工具回复却包含损坏 JSON，则返回 `error`。两者都不执行工具。`message_delta` 的输出 token 是累计值，更新计数即可，不应将每条累计值再次相加。

## 第四步：完整替换 Agent，接入请求和中断处理

本章对 Agent 有三个一起发生的修改：构造函数增加 `stream=False`；`run()` 通过 `yield from self._request()` 取得完整回复；用 `try/except/finally` 保证中断后补齐未完成调用。不要只给构造函数加参数，却漏掉运行和清理路径。

将 `minicode/agent.py` 整个替换为以下代码。它保留第三章的用量和停止原因判断，不需要预先加入会话对象或未来的 hooks：

```python
"""只编排内部消息与工具；不认识任何服务商的请求字段。"""
from dataclasses import asdict

from .types import Message, ModelReply, ToolResult, Usage, message_text, tool_calls


class Agent:
    def __init__(self, model, runner, system_prompt="你是一个编程助手。", *,
                 max_turns=20, stream=False):
        if max_turns < 1:
            raise ValueError("max_turns 必须至少为 1")
        self.model, self.runner = model, runner
        self.max_turns, self.stream = max_turns, stream
        self.history = [Message.text("system", system_prompt)]
        self.usage = Usage()

    def run(self, prompt: str):
        """一个用户任务，产出事件，正常消费时以唯一的 result 事件结束。"""
        self.repair_pending()
        self.history.append(Message.text("user", prompt))
        turn = 0
        try:
            for turn in range(1, self.max_turns + 1):
                try:
                    reply = yield from self._request()
                    if not isinstance(reply, ModelReply) or reply.message.role != "assistant":
                        raise ValueError("模型必须返回 assistant ModelReply")
                except Exception as error:
                    yield self._result("error", f"模型请求失败：{type(error).__name__}: {error}", turn)
                    return
                self.usage.input_tokens += reply.usage.input_tokens
                self.usage.output_tokens += reply.usage.output_tokens
                text = message_text(reply.message)
                calls = tool_calls(reply.message)
                if reply.stop_reason not in {"stop", "tool_calls"}:
                    # 不回放截断的调用或原始协议数据，也不执行看起来已经完整的参数。
                    if text:
                        self.history.append(Message.text("assistant", text))
                    yield {"type": "assistant", "message": Message.text("assistant", text),
                           "text": text, "tool_calls": []}
                    status = reply.stop_reason if reply.stop_reason in {"length", "refusal", "cancelled"} else "error"
                    yield self._result(status, reply.error or text or f"模型停止：{reply.stop_reason}", turn)
                    return
                if bool(calls) != (reply.stop_reason == "tool_calls"):
                    yield self._result("error", "模型停止原因与工具调用不一致", turn)
                    return
                prior_ids = {call.id for message in self.history for call in tool_calls(message)}
                ids = [call.id for call in calls]
                if any(not value for value in ids) or len(set(ids)) != len(ids) or prior_ids.intersection(ids):
                    yield self._result("error", "模型返回了缺失或重复的工具调用 ID", turn)
                    return
                self.history.append(reply.message)
                yield {"type": "assistant", "message": reply.message, "text": text, "tool_calls": calls}
                if reply.stop_reason == "stop":
                    yield self._result("success", text, turn)
                    return
                for call in calls:
                    try:
                        result = self.runner.run(call)
                    except Exception as error:
                        result = ToolResult(call.id, f"工具执行层失败：{type(error).__name__}: {error}", True)
                    self.history.append(Message("tool", [result]))
                    yield self._tool_event(result)
            yield self._result("max_turns", f"已达到最大轮数 {self.max_turns}", turn)
        except KeyboardInterrupt:
            for result in self.repair_pending():
                yield self._tool_event(result)
            yield self._result("cancelled", "用户中断了任务", turn)
        finally:
            # 消费者 close() 或 Ctrl+C 都不能留下没有结果的调用。
            self.repair_pending()

    def _request(self):
        if not self.stream:
            return self.model.complete(self.history, self.runner.specs())
        reply = None
        stream = iter(self.model.stream(self.history, self.runner.specs()))
        try:
            for event in stream:
                if reply is not None:
                    raise ValueError("完成事件之后仍收到流数据")
                if event.type == "done":
                    if event.reply is None:
                        raise ValueError("完成事件缺少最终回复")
                    reply = event.reply
                elif event.type == "text_delta":
                    yield {"type": "text_delta", "text": event.text}
                elif event.type == "tool_call_delta":
                    # 参数分片仅供观察；执行必须等到最终回复通过检查。
                    yield {"type": "tool_call_delta", "call_id": event.call_id,
                           "arguments_delta": event.arguments_delta}
                else:
                    raise ValueError(f"未知流事件：{event.type}")
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        if reply is None:
            raise ValueError("流在完成事件之前结束")
        return reply

    def repair_pending(self, note="调用被中断，结果未知。请先检查现状，不要假定操作没有发生。"):
        answered = {part.call_id for message in self.history for part in message.content
                    if isinstance(part, ToolResult)}
        results = []
        for message in list(self.history):
            for call in tool_calls(message):
                if call.id not in answered:
                    result = ToolResult(call.id, note, True)
                    self.history.append(Message("tool", [result]))
                    answered.add(call.id)
                    results.append(result)
        return results

    @staticmethod
    def _tool_event(result):
        return {"type": "tool_result", "result": result, "call_id": result.call_id,
                "content": result.content, "is_error": result.is_error}

    def _result(self, status, text, turns):
        return {"type": "result", "status": status, "text": text,
                "turns": turns, "usage": asdict(self.usage)}
```

现在才可以调用：

```python
agent = Agent(model, runner, stream=True)
for event in agent.run("查看 README，并说明项目用途"):
    if event["type"] == "text_delta":
        print(event["text"], end="", flush=True)
    elif event["type"] == "result":
        print("\n状态：", event["status"])
```

这是显示事件的解释片段，`model` 与带权限的 `runner` 沿用前章对象。最后的 assistant 事件仍包含完整文本；终端若已经打印增量，就不要再重复打印完整文本。

## 第五步：中断时先保持历史一致

这里有两种不同情况：

1. 模型流中断：本轮完整回复尚未收到，不执行部分工具调用，也不把半成品当作完整 assistant 消息保存。
2. 工具执行时中断：assistant 调用已经记录。为尚未返回的调用补上带 ID 的中断错误结果，并说明外部影响未知，不能自动重跑写操作。

`repair_pending()` 是本章刚加入的方法，第二章没有它。它在任务开始时、键盘中断时，以及生成器退出的 `finally` 中运行；只补结果，不执行工具。传输层的上下文管理器负责关闭 HTTP 响应。

当前实现不自动恢复半途断开的流，也不自动重试带外部副作用的工具。重新发起请求前，先依据记录判断哪些工具确实执行过。

## 验收

```bash
uv run python examples/08_streaming.py
uv run python -m pytest tests/test_streaming.py tests/test_providers.py -q
uv run python -m pytest tests -q
```

三个协议都应输出 `结果是 5。`，并显示两个文本增量 `结果是 ` 和 `5。`。

测试还覆盖交错工具参数、流内错误、缺少终止事件、SSE 多行 data、思考签名保存和截断后不执行工具。把测试中的最后一个终止事件删除，应该得到错误而不是 success。

沿用第三章真实入口，现在加入 `--stream`：

```bash
uv run python examples/live_model.py --env-prefix LLM --api openai-chat --stream
uv run python examples/live_model.py --env-prefix LLM --api openai-responses --stream
uv run python examples/live_model.py --env-prefix LLM --api anthropic-messages --stream
```

只对当前账号支持的协议执行对应命令。真实 HTTP 流的速度、代理缓冲和账号模型差异需要在线观察；离线测试检查解析与循环语义。

协议资料：[Chat 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[Responses 流事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)、[Anthropic 流事件](https://platform.claude.com/docs/en/build-with-claude/streaming)。
