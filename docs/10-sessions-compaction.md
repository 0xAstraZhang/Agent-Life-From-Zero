# 第 10 章：会话与压缩，保存事实，缩短下一次请求

上一章：[上下文](09-context.md) · 下一章：[Hooks 与规划](11-hooks-planning.md)

前面的 `Agent.history` 只存在于内存中。
程序退出后，已经读过哪些文件、哪些工具成功或失败，都无法恢复。
另一方面，把全部历史永远发给模型也会逐渐超出上下文窗口。

本章分别实现两件事：保存完整会话，以及生成较短的模型请求。
它们使用同一套内部消息，但生命周期不同。

新增 [session.py](../minicode/session.py) 和 [compact.py](../minicode/compact.py)。
主循环与三个协议适配器不需要认识“会话文件”这个概念。

## 验收任务

```bash
uv run python examples/10_sessions.py
```

预期输出：

```text
下一步运行测试。
完整历史 5 条，模型请求 3 条
```

第一次运行保存任务；第二次从保存的消息恢复。
示例故意把压缩阈值设成 1，让两轮短对话也触发摘要，便于观察。
实际使用时应设置更合理的阈值。

## 第一步：保存内部消息

会话文件用 JSONL，每行是一个 JSON 对象。
第一行保存格式版本与来源，后面每行保存一条内部消息。

```json
{"type":"session","version":1,"source":{"api":"fake","model":"script"}}
{"type":"message","message":{"role":"user","content":[{"type":"text","text":"先找到测试入口"}],"provider_data":{}}}
```

序列化使用第 1 章类型模块的 `message_to_dict()`；读取使用 `message_from_dict()`。
这样即使会话来自不同协议，保存的核心结构仍然一致。

`provider_data` 中可能包含同一服务商续轮所需的不透明数据。
会话层原样保存它，不解释 reasoning item、thinking signature 或服务商消息 ID。
适配器负责核对来源，决定是否可以回传。

`SessionStore` 顶层来源只允许保存 `api`、`model`、`provider`、`base_url`。
不会把 API Key 写进会话头。
会话正文仍可能包含工具读到的私人文件，所以不要把会话目录提交到 Git。

## 第二步：完整写入后再替换

本教程每次保存一个完整快照，而不是更新文件中的某几行。
先把新内容写到同目录的临时文件，刷新后用 `os.replace()` 替换目标。

```python
store = SessionStore(".minicode/session.jsonl", source={
    "api": "openai-responses",
    "model": model_name,
})
store.save(agent.history)
history = store.load()
```

如果程序写到一半退出，已有快照仍能读取。
这个简化实现适合单进程、单会话写入；它不是多人共享的数据库。

`load()` 会检查格式版本与消息类型。
损坏文件应报错，不能悄悄跳过某条工具结果后继续请求模型。

## 第三步：中断后不能直接重做工具

考虑这个顺序：

```text
assistant: Write 调用
进程执行了写文件
程序退出，工具结果还没保存
```

恢复时只知道“有一个调用，没有结果”。
不能推断文件一定没写，也不能自动重新执行。

`repair_pending()` 在原来的调用后补上一个 `is_error=True` 的结果：

```text
调用被中断，结果未知。请先检查现状，不要假定操作未发生，也不要直接重做。
```

如果同一条 assistant 消息有两个调用，其中一个已有结果，只补缺失的那一个。
补齐的位置必须在后续用户消息之前，保证工具往返仍然相邻。

`run_session()` 在 assistant、工具结果、终止事件处保存。
消费者关闭生成器时，它先关闭 Agent 的生成器，让 Agent 修补未完成调用，再保存历史。

```python
from minicode.session import run_session

for event in run_session(agent, "继续任务", store):
    display(event)
```

`display` 是这里代表界面处理的占位名称；完整例子直接读取最后一个事件。

## 第四步：把完整记录和模型请求分开

压缩不直接修改 `agent.history`。
`CompactingModel` 包装已有模型，在发送前创建一份请求副本。

```text
Agent 的完整历史
        ↓
CompactingModel：摘要旧轮次，保留近期轮次
        ↓
原来的 Chat / Responses / Anthropic 适配器
```

这个边界让调试仍然能查看原始工具输出。
也让摘要失败时可以直接退回原请求，而不会丢失历史。

```python
model = CompactingModel(raw_model, threshold_chars=48_000, keep_turns=2)
agent = Agent(model, runner, history=store.load())
```

阈值按序列化字符数估算，包含协议保留数据；它不是精确 token 计数。
本章也没有保证任何一次请求都必然低于服务商的上下文上限。
单轮任务特别长时，需要进一步减少工具输出或增加细粒度压缩。

## 第五步：不能把工具往返切成两半

内部 `role="user"` 表示真正的新用户轮，工具结果使用独立 `role="tool"`。
因此可以在用户轮边界切分：

```text
system
旧用户轮：user → assistant(call) → tool(result) → assistant
近期用户轮：user → assistant(call) → tool(result)
```

整个旧轮次一起进入摘要，近期轮次完整保留。
不能按“只保留最后 20 条消息”随意切片，那可能留下没有调用的工具结果。

`keep_turns` 至少为 1，所以最新的用户要求保持原文。
系统提示词也保留；摘要放在普通用户上下文中，不提升成新的系统指令。

## 第六步：验证摘要，再接受它

摘要模型必须正常结束，且返回非空文本。
截断回复或网络异常不会成为新的摘要。
失败时记录 `last_error`，原始请求照常交给模型。

摘要提示词要求保留：用户目标、约束、已验证的结果、未完成事项、相关文件路径，以及不确定性。
它并不能保证摘要绝不遗漏；完整日志是定位遗漏的依据。

相同旧前缀使用哈希作为缓存键。
同一用户任务内部多次调用工具，不会每次都重写同一份摘要。
摘要消耗的 token 会加到最终 `ModelReply.usage`，不会在费用统计中消失。

完整组合示例见 [10_sessions.py](../examples/10_sessions.py)。

## 离线检查

```bash
uv run pytest tests/test_extensions.py -k 'session or compaction' -q
```

检查包括：来源数据回放、缺失结果修补、关闭生成器后的保存、损坏版本、摘要失败回退、
最新用户要求保留、原历史不变、缓存和摘要用量统计。

## 开源实现中的边界

[OpenCode 的统一消息模型](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/schema/messages.ts)
把文本、调用、结果和服务商元数据放进有类型的结构。
本章沿用同样的原则：压缩算法处理内部内容，协议保留数据交给适配器处理。

## 自检与练习

1. 在一个包含两次工具调用的会话里删去一个结果，验证恢复只补这一个结果。
2. 让摘要模型返回 `length`，确认完整历史没有被替换。
3. 记录压缩前后请求字符数，检查压缩是否真的减少了请求。
4. 如果只剩一个用户轮却已经很长，应优先限制哪类工具输出？说明原因。
