# 第 8 章：先显示文本增量，再接回工具循环

[上一章：权限](07-permissions.md) · [目录](README.md) · [下一章：上下文](09-context.md)

前面程序等模型说完才显示回答。本章先让终端显示逐段到达的文字，再把完整回复接回已有循环。工具仍在完整调用到达之后执行。

先在教程仓库根目录导出本章，再进入练习目录：

```bash
uv run python scripts/check_tutorial.py --chapter 08 --export /tmp/minicode-ch08
cd /tmp/minicode-ch08
uv sync
```

导出目标须为空或尚不存在。以下命令在练习目录执行；真实调用沿用第 00 章的环境变量配置。

## 1. 先只显示一段真实回答

```bash
uv run python examples/08_streaming.py --step text
```

真实模型用两句话解释 Agent。你会看见文本逐段出现，末尾显示本轮状态。先不调用工具，只观察“数据一段段到达”这件事。

完整检查点是 [streaming_prototype.py 中的 request_stream](../lessons/streaming_prototype.py)。它的正常路径只有这几步：

```python
reply = None
for event in model.stream(messages, tools):
    if event.type == "text_delta":
        print(event.text, end="", flush=True)
    elif event.type == "done":
        reply = event.reply
```

`text_delta` 负责显示，`done` 携带本轮完整 `ModelReply`。完整函数在循环之后返回 `reply`；没有收到它就报告断流。先运行这个版本，确认模型接口、终端显示与完整回复各做什么。

上面是函数中的正常路径片段；所需资源关闭与结束检查已放在链接的可运行版本中。第 5 节再逐一解释它们。

## 2. 用完整回复继续工具往返

现在运行第二个最小检查点：

```bash
uv run python examples/08_streaming.py --step tools
```

模型请求 `add`，本地执行，然后流式显示最终回答。[stream_roundtrip()](../lessons/streaming_prototype.py)沿用第一章的循环，只把等待回复的一行换掉：

```python
# 前章：
reply = model.complete(history, runner.specs())
# 本章：
reply = request_stream(model, history, runner.specs())
```

取得完整回复之后，原来的三步仍然相同：

```python
history.append(reply.message)
for call in tool_calls(reply.message):
    result = runner.run(call)
    history.append(Message("tool", [result]))
```

这两段是循环里的替换点，不是独立脚本。完整函数在无调用时返回回答，有调用时执行并进入下一轮；本练习最多请求 4 轮。

本章不从终端显示过的文字重新拼历史。完整 assistant 消息可能同时含文本、工具调用以及续轮数据，应该直接使用 `done.reply.message`。

工具参数增量可能是 `{"a":`，下一段才是 `2,"b":3}`。收到参数片段时不运行工具，等完整回复通过已有停止状态检查后再执行。这是流式工具循环的核心区别。

## 3. 再把原型并入完整 Agent

前两个检查点已经解释了变化。现在给现有 Agent 加流式选项：

1. 模型接口增加 `stream()`，每次产生一个 `StreamEvent`。
2. Agent 的请求步骤选择 `complete()` 或 `stream()`。
3. 流式路径把显示事件交给终端，取得完整回复后回到原有循环。

```python
agent = Agent(model, runner, stream=True)
for event in agent.run("请调用 add 计算 2 加 3，再回答"):
    if event["type"] == "text_delta":
        print(event["text"], end="", flush=True)
    elif event["type"] == "result":
        print("\n任务状态：", event["status"])
```

这里的 `runner` 仍可使用上一章的权限包装器。不要重建另一套审批或工具执行。终端已经显示了增量，结束时只打印状态，避免再显示一遍完整回答。

```bash
uv run python examples/08_streaming.py --step enhanced
```

完整文件分别是 [StreamEvent](../lessons/stages/08/minicode/types.py)、[模型接口](../minicode/model.py)和[本章 Agent](../lessons/stages/08/minicode/agent.py)。现在对照 `_request()` 与刚才的 `request_stream()`，再看流式分支如何接回 `run()`；无需一开始就整段抄写 Agent。

## 4. 看到增量之后，再实现传输与协议解析

统一事件先保留三个含义：

| 事件 | 用途 |
| --- | --- |
| text_delta | 显示文本 |
| tool_call_delta | 观察参数片段，不执行 |
| done + ModelReply | 将完整回复交回循环 |

HTTP 返回的是 SSE。`httpx` 读取字节与文本行，[sse_data()](../lessons/stages/08/minicode/providers/base.py)按空行组合一个事件，适配器解释事件数据。网络读取一次不等于收到一个完整事件。

```text
data: {"type":"response.output_text.delta","delta":"你好"}

```

先为你使用的协议写文本增量与完成事件，再扩展工具参数；其他协议仍是选修：

- [Chat Completions](../lessons/stages/08/minicode/providers/chat.py)：显示 `delta.content`；工具片段按 `index` 分别积累。取得完成标记和用量后，将合成响应交给原来的 `decode()`。
- [Responses](../lessons/stages/08/minicode/providers/responses.py)：显示 `response.output_text.delta`；结束事件中的完整 response 交给 `decode()`。参数增量的 item ID 要映射到实际 call ID。
- [Anthropic](../lessons/stages/08/minicode/providers/anthropic.py)：按内容块积累文本或参数，直到 `message_stop` 再解码整条消息。思考块与签名随对应内容块保存。

复用非流式 `decode()`，停止状态和工具语义就不会出现两套规则。

## 5. 最后补上断流、中断与清理

先用具体故障检验刚才的流程：

| 故障 | 需要补什么 |
| --- | --- |
| 文本到一半连接关闭，没有 done | 报告断流，不执行参数片段 |
| 收到完整回复后还有额外数据 | 拒绝不一致的事件序列 |
| 完整回复标为 length | 保留已有终止规则，不执行截断调用 |
| 用户停止读取流 | 关闭迭代器与 HTTP 响应 |
| 工具已开始执行时用户中断 | 为未完成调用补带 ID 的中断结果 |

`request_stream()` 的 `finally` 关闭流，循环后的检查拒绝缺失完整回复。完整 Agent 的 `repair_pending()` 则处理另一种情况：调用已写入历史，但工具结果尚未返回。补记只说明“结果未知”，不自动重跑写操作。

实现 SSE 分帧后，再测试多行 `data:`、注释心跳、多个工具参数交错和最后才出现的用量。Anthropic 的输出用量事件是累计值，不应每次再次相加。这些都是在正常文本与工具流程跑通后增加的验证。

## 6. 验收每个检查点

默认命令使用真实 API；离线回归明确传 `--offline`：

```bash
uv run python examples/08_streaming.py --step text --offline
uv run python examples/08_streaming.py --step tools --offline
uv run python examples/08_streaming.py --step enhanced --offline
uv run python examples/08_streaming.py --step transport --offline
uv run python -m pytest tests/test_streaming.py tests/test_providers.py -q
```

前三条验证显示、完整回复与循环组合，最后一条实验用固定 HTTP SSE 数据检查三个协议。再删除测试流中的结束事件，确认工具没有执行；这比只确认终端出现文字更能证明循环正确。

本章新增 [streaming_prototype.py](../lessons/streaming_prototype.py)和[实验入口](../examples/08_streaming.py)，替换模型、测试替身、适配器与 Agent 的本章版本，完整清单见[阶段文件](../lessons/stages.json)。

协议资料：[Chat 流事件](https://developers.openai.com/api/reference/resources/chat/subresources/completions/streaming-events)、[Responses 流事件](https://developers.openai.com/api/reference/resources/responses/streaming-events)、[Anthropic 流事件](https://platform.claude.com/docs/en/build-with-claude/streaming)。下一章向同一个模型请求补充项目上下文。
