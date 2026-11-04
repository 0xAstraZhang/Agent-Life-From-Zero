# 第 8 章：流式输出、完整回复与中断

[上一章：权限](07-permissions.md) · [目录](README.md) · [下一章：上下文](09-context.md)

前面每次请求都等待完整响应。工具执行可能只有几毫秒，模型却要生成几秒钟，用户这段时间什么都看不到。流式输出把文本到达的过程显示出来。

这一章要守住一个边界：增量是界面事件，完整回复才可以进入历史和触发工具。收到半段 JSON 不等于模型已经决定执行。

## 本章新增什么

- [base.py](../minicode/providers/base.py) 中的 SSE 分帧和 `_events()`。
- 三个适配器各自的 `stream()` 状态机。
- [Agent._request()](../minicode/agent.py) 对统一流事件的消费。
- [08_streaming.py](../examples/08_streaming.py) 的三协议离线演示。

前面章节的最小循环只调用 `complete()`。在这里才增加 `stream=True` 分支。权限、工具执行和历史组织不需要另写一套。

## 第一步：把 SSE 与模型语义分开

SSE 使用空行划分事件，一条事件可以有多行 `data:`，也可以夹杂注释心跳：

```text
: heartbeat

data: {"type":"response.output_text.delta","delta":"你好"}

```

`httpx` 负责 HTTP 字节流，`sse_data()` 负责按行组合 SSE 帧，协议适配器才解释 JSON 中的字段。

不要假定“网络读取一次”就等于“一个事件”。也不要把连接关闭直接当成成功：网络错误和正常 EOF 都可能发生在回复生成一半时。

## 第二步：定义统一事件

在第 1 章的公共类型旁增加：

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

## 第四步：接入现有 Agent

构造 Agent 时打开流式模式：

```python
agent = Agent(model, runner, stream=True)
for event in agent.run("查看 README，并说明项目用途"):
    if event["type"] == "text_delta":
        print(event["text"], end="", flush=True)
    elif event["type"] == "result":
        print("\n状态：", event["status"])
```

内部 `_request()` 迭代统一事件，转发文本增量，拿到最终 `ModelReply` 后返回主循环。主循环继续走原来的停止原因判断、消息记录和工具执行。

最后的 assistant 事件仍包含完整文本。终端若已经打印了增量，就不要再重复打印一次完整文本。日志可以同时保留过程事件和最终结果。

## 第五步：中断时先保持历史一致

这里有两种不同情况：

1. 模型流中断：本轮完整回复尚未收到，不执行部分工具调用，也不把半成品当作完整 assistant 消息保存。
2. 工具执行时中断：assistant 调用已经记录。为尚未返回的调用补上带 ID 的中断错误结果，并说明外部影响未知，不能自动重跑写操作。

第 2 章的 `repair_pending()` 负责第二种配对修复。传输层的上下文管理器负责关闭第一种情况下的 HTTP 响应。

当前实现不自动恢复半途断开的流，也不自动重试带外部副作用的工具。重新发起请求前，先依据记录判断哪些工具确实执行过。

## 验收

```bash
uv run python examples/08_streaming.py
uv run pytest tests/test_providers.py tests/test_core.py -q
```

三个协议都应输出 `结果是 5。`，并显示两个文本增量 `结果是 ` 和 `5。`。

测试还覆盖交错工具参数、流内错误、缺少终止事件、SSE 多行 data、思考签名保存和截断后不执行工具。把测试中的最后一个终止事件删除，应该得到错误而不是 success。

真实 HTTP 流的速度、代理缓冲和账号模型差异，需要在明确配置真实接口后观察。离线测试检查的是解析与循环语义。

协议资料：[Chat 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[Responses 流事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)、[Anthropic 流事件](https://platform.claude.com/docs/en/build-with-claude/streaming)。
