# 第 5 章：接入 Anthropic Messages

[上一章：Responses](04-responses.md) · [目录](README.md) · [下一章：编码工具](06-coding-tools.md)

这一章接上第三种协议。它的工具调用是 assistant 消息中的 `tool_use` 块，工具结果则放进 user 消息的 `tool_result` 块。

如果此时需要复制 Agent 循环，说明前面的边界还没建立好。本章只新增协议适配和契约测试，让前两章建立的内部结构继续工作。

## 本章新增什么

实现：[providers/anthropic.py](../minicode/providers/anthropic.py)。

运行：[05_anthropic.py](../examples/05_anthropic.py)。

检查：[test_providers.py](../tests/test_providers.py) 中参数化为 `anthropic-messages` 的测试。

按下面顺序阅读：认证请求头 → system 提取 → 内容块转换 → 结果合并 → 回复解码。流式部分在第 8 章展开。

## 第一步：构造请求

`base_url` 使用 `https://api.anthropic.com/v1`，适配器再添加 `/messages`。认证和版本由适配器提供：

```python
headers = {
    "x-api-key": config.api_key,
    "anthropic-version": "2023-06-01",
}
```

与前两个协议不同，system 指令在顶层。`encode()` 收集内部 system 消息，合成请求的 `system` 字段；其余内容进入 `messages`。

```python
body = {
    "model": config.model,
    "max_tokens": config.max_tokens,
    "system": "你是一个编程助手。",
    "messages": [{"role": "user", "content": [
        {"type": "text", "text": "2 加 3 等于多少？"}
    ]}],
}
```

这里仍然没有执行工具。适配器只描述当前对话。

## 第二步：转换工具定义和调用

工具 schema 放进 `input_schema`：

```python
wire_tool = {
    "name": spec.name,
    "description": spec.description,
    "input_schema": spec.parameters,
}
```

模型返回的工具块类似：

```json
{
  "type": "tool_use",
  "id": "call_1",
  "name": "add",
  "input": {"a": 2, "b": 3}
}
```

`input` 已经是对象，不像前两种协议那样是 JSON 字符串。`decode()` 将它转换为同一个 `ToolCall("call_1", "add", {"a": 2, "b": 3})`。

模型回复中可以同时有文本和工具块。按顺序遍历 `content`，保留各块的先后关系。不要只取第一个文本块，也不要假设每次只会调用一个工具。

## 第三步：把一批结果合成 user 内容块

内部仍然记录：

```python
Message("tool", [ToolResult("call_a", "5")])
Message("tool", [ToolResult("call_b", "权限拒绝", is_error=True)])
```

适配器把连续的两条结果合并：

```json
{
  "role": "user",
  "content": [
    {"type": "tool_result", "tool_use_id": "call_a", "content": "5", "is_error": false},
    {"type": "tool_result", "tool_use_id": "call_b", "content": "权限拒绝", "is_error": true}
  ]
}
```

为什么要合并？这两个结果共同回答上一条 assistant 消息中的两个调用。它们应紧接那条消息，先交齐结果，再继续普通用户文本。

`is_error` 在此协议中有原生字段。内部错误语义保持不变，具体如何在线路上表达，由适配器决定。

内部的 `role="tool"` 并不要求外部协议也存在 tool 角色。内部类型表达的是业务语义，不是某个 API 的字段复刻。

## 第四步：保留思考块和结束状态

思考块和 redacted thinking 数据放进原始内容元数据，同一路由续轮时原样回放。签名不作为普通可见文本显示，也不由 Agent 修改。

这条规则与上一章一致：只有来源相同、内部消息未编辑，才能回放原始数据。跨模型、跨协议时只重新编码公共内容；这不保证跨模型推理状态连续。

| Messages 停止原因 | 内部状态 |
| --- | --- |
| end_turn、stop_sequence | `stop` |
| tool_use | `tool_calls` |
| max_tokens | `length` |
| refusal | `refusal` |
| 其他未支持状态 | `error` |

`pause_turn` 等服务端工具流程不在这个教学客户端的范围内，不能把它们当成最终回答。

输入 token 统计还会计入返回的 cache read 和 cache creation 数量。我们先统一总量；需要精确费用时，再分别记录缓存类别和对应价格，不能把这三个数量套进同一价格后声称得到准确账单。

## 第五步：运行相同的工具闭环

```bash
uv run python examples/05_anthropic.py
```

预期输出：

```text
anthropic-messages: 两次 HTTP 请求，一次本地工具调用
结果是 5。
```

```bash
uv run pytest tests/test_providers.py -k 'anthropic' -q
```

检查结果中至少应包含：顶层 system、`input_schema`、多结果合并、原生 `is_error`、截断不执行工具，以及原始签名的回放。

再一起跑三种协议：

```bash
uv run pytest tests/test_providers.py -q
```

此时我们的第一项跨协议验收完成了：相同的 ToolRunner、Agent 和内部历史，通过三种编码方式完成同一种任务。

## 对照开源实现

[OpenCode 的 Anthropic 协议层](https://github.com/anomalyco/opencode/blob/59e95cc5394b7714451608099ef4db7bffc904b6/packages/ai/src/protocols/anthropic-messages.ts)把内部消息转换为 Messages 请求，并处理内容块和结果合并。

[pi 的 Anthropic 适配器](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/api/anthropic-messages.ts)可用于追踪工具结果、消息和工具声明的转换。阅读时只追这三条路径，不必一次理解全部模型能力。

练习：让假服务一次请求两个加法调用，其中一个参数类型错误。确认程序执行合法调用，为另一个生成错误结果，并在下一次请求的同一个 user 内容数组里回传两者。

协议资料：[Anthropic 工具调用与结果](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)。当前实现不包含多模态输入、服务端工具和思考模式配置；它会保留已经收到的思考块，但不会替你开启对应模型能力。
