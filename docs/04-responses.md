# 第 4 章：接入 OpenAI Responses

先回到教程仓库根目录，执行下面的导出命令；随后进入独立的本章目录。手写时保留自己的练习目录，用导出版本核对文件清单和差异：

```bash
uv run python scripts/check_tutorial.py --chapter 04 --export /tmp/minicode-ch04
cd /tmp/minicode-ch04
uv sync
```

以下运行和测试命令在本章练习目录中执行。目录只含第 00–04 章的代码与测试；文件增量由[阶段清单](../lessons/stages.json)记录。导出目标必须为空，不会覆盖已有练习。

[上一章：Chat Completions](03-chat-completions.md) · [目录](README.md) · [下一章：Anthropic](05-anthropic.md)

上一章已经有一个能调用真实 HTTP 接口的 Agent。这一章新增 `ResponsesModel`，保持 `Agent`、`ToolRunner` 和加法工具不变。

本章的验收是同一条“提问 → 调用工具 → 回传结果 → 回答”流程，只更换协议配置就能运行。我们还要处理 Responses 与 Chat Completions 的一个根本差别：输入和输出是不同类型的 item，不只是角色消息。

## 本章新增什么

实现位于 [providers/responses.py](../lessons/stages/04/minicode/providers/responses.py)。运行入口是 [04_responses.py](../examples/04_responses.py)。新增 [test_ch04.py](../tests/test_ch04.py)，并保留第三章测试。测试可以共享内部类型，不能提前导入第五章模块。

先实现 `encode()`，再实现 `decode()`，最后在 `create_model()` 中注册 `openai-responses`。第 8 章再读 `stream()`。

本章只新增或替换以下完整文件，其他文件保留上一章版本：

| 操作 | 文件 |
| --- | --- |
| 新增适配器 | [providers/responses.py](../lessons/stages/04/minicode/providers/responses.py) |
| 扩展协议注册 | [providers/__init__.py](../lessons/stages/04/minicode/providers/__init__.py) |
| 增加本协议假 HTTP 数据 | [providers/demo.py](../lessons/stages/04/minicode/providers/demo.py) |
| 新增实验与测试 | [本章实验](../examples/04_responses.py)、[本章测试](../tests/test_ch04.py) |

工厂只注册截至本章已经创建的协议，实验仍调用非流式 `Agent(model, runner)`。下文的短数据块用于解释转换；可运行的完整文件以上表为准。

## 第一步：认识三个不同的 item

| 内部内容 | Responses item |
| --- | --- |
| 用户或助手文本 | message |
| `ToolCall` | `function_call` |
| `ToolResult` | `function_call_output` |

内部消息可以包含多块内容。适配器按顺序展开，不能只读取 `output_text`，因为这样会丢掉工具调用。

假设模型输出如下：

```json
{
  "status": "completed",
  "output": [
    {"type": "function_call", "id": "fc_1", "call_id": "call_1",
     "name": "add", "arguments": "{\"a\":2,\"b\":3}"}
  ],
  "usage": {"input_tokens": 10, "output_tokens": 5}
}
```

解码后的内部对象仍然是：

```python
ToolCall(id="call_1", name="add", arguments={"a": 2, "b": 3})
```

这里必须用 `call_id`，不能用 `id`。`fc_1` 标识响应中的 item；`call_1` 标识工具调用和结果的关联。两者碰巧看起来都像 ID，却承担不同职责。

## 第二步：编码工具和结果

Responses 的工具声明比 Chat 少一层 `function`：

```python
wire_tool = {
    "type": "function",
    "name": spec.name,
    "description": spec.description,
    "parameters": spec.parameters,
    "strict": False,
}
```

保持 `strict=False`，让第 2 章中可选字段的含义不随协议切换改变。工具的本地校验没有变化。

调用成功后，第二次请求的 `input` 需要包含原调用和结果：

```json
[
  {"role": "user", "content": "2 加 3 等于多少？"},
  {"type": "function_call", "id": "fc_1", "call_id": "call_1",
   "name": "add", "arguments": "{\"a\":2,\"b\":3}"},
  {"type": "function_call_output", "call_id": "call_1", "output": "5"}
]
```

`ToolResult.is_error` 仍保留在内部记录里。此协议的文本结果由适配器加上 `[tool error]` 前缀表达失败，Agent 不需要知道这条编码规则。

## 第三步：自己持有对话历史

Responses 可以使用服务端会话能力。本教程统一采用客户端保存完整历史，每次发送 `input`，不使用 `previous_response_id`。这样三个协议共享同一种会话管理方式，第 10 章也能用一个本地文件恢复历史。

对应请求参数是：

```python
body = {
    "model": config.model,
    "input": items,
    "store": False,
    "include": ["reasoning.encrypted_content"],
    "max_output_tokens": config.max_tokens,
}
```

文本和工具调用属于公共内部结构。reasoning items 中不能被公共结构表达的数据进入 `provider_data`，同一路由续轮时原样回放。

不要把 reasoning item 当作用户可见文本，也不要删掉它后声称保留了完整的推理上下文。它可能包含后续请求需要的加密数据。

`provider_data` 同时包含协议、服务商、模型和 base URL，以及内部内容指纹。消息被编辑时，原始 item 不再覆盖内部内容；切换路由时，不回放来源不同的专有数据。

## 第四步：区分响应完成与任务完成

`status="completed"` 表示这一次模型响应已经完成。若输出仍有 `function_call`，Agent 还必须执行工具并继续下一轮。

| Responses 状态 | 内部状态 |
| --- | --- |
| completed，包含函数调用 | `tool_calls` |
| completed，没有函数调用 | `stop` |
| incomplete，原因 max_output_tokens | `length` |
| incomplete，原因 content_filter | `refusal` |
| cancelled | `cancelled` |
| failed 或未支持状态 | `error` |

即使响应包含完整的参数字符串，只要整个响应是 `incomplete`，循环就不能执行那些调用。否则模型只生成了一半计划，程序已经开始修改文件。

适配器同时识别文本中的 refusal 内容块。它们不会被误判为普通成功回复。

## 第五步：用相同流程验收

```bash
uv run python examples/04_responses.py
```

预期：

```text
openai-responses: 两次 HTTP 请求，一次本地工具调用
结果是 5。
```

对照上一章入口，两者只改变了传入 `run_demo()` 的协议名。加法函数、Agent 和 ToolRunner 完全相同。

```bash
uv run python -m pytest tests/test_ch03.py tests/test_ch04.py -q
```

重点看本章的两个测试：完整 HTTP 工具往返成功；item ID 不会被误用为结果的 `call_id`，原调用 item 的 ID 仍保留在下一次请求中。

再用本章已实现的 `remember()` / `replay()` 写两项练习测试：同来源未编辑消息保留原始 reasoning item；修改内部内容或切换模型后不回放旧的原始数据。

这些测试不需要真实账号，也不会发送网络请求。沿用第三章真实入口，只替换协议名：

```bash
uv run python examples/live_model.py --env-prefix LLM --api openai-responses
```

该命令要求选中的服务确实支持 Responses。DeepSeek 使用相同 API 根地址；其他服务请在所选环境变量组中设置它的 Responses 地址。入口不调用尚未出现的流式参数。

## 与开源实现对照

[Codex 的 ResponseItem](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/protocol/src/models.rs)把 message、reasoning、function call 和结果区分开；它的 function call 同时保留 item ID 和 call ID。本章借鉴这一保留信息的方式。

[pi 的 Responses 转换](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/api/openai-responses-shared.ts)适合对照查看统一消息如何变成 input items。

这里没有宣称 Codex 提供三种协议。所引用版本的 Codex 只支持 Responses。minicode 的多协议结构还要参考 pi 和 OpenCode。

练习：把假响应中的 `id` 改成 `fc_other`，保持 `call_id` 不变。结果应仍然正确配对。再将状态改为 incomplete，确认加法工具没有执行。

协议资料：[OpenAI Responses 迁移指南](https://developers.openai.com/api/docs/guides/migrate-to-responses)。本章支持文本、本地 function 工具和 reasoning item 回放；托管搜索、图像生成等其他 output item 会明确报不支持。
