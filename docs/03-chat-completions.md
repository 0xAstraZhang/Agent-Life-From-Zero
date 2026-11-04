# 第 3 章：接入 OpenAI Chat Completions

先回到教程仓库根目录，执行下面的导出命令；随后进入独立的本章目录。手写时保留自己的练习目录，用导出版本核对文件清单和差异：

```bash
uv run python scripts/check_tutorial.py --chapter 03 --export /tmp/minicode-ch03
cd /tmp/minicode-ch03
uv sync
```

以下运行和测试命令在本章练习目录中执行。目录只含第 00–03 章的代码与测试；文件增量由[阶段清单](../lessons/stages.json)记录。导出目标必须为空，不会覆盖已有练习。

[上一章：统一工具](02-tools.md) · [目录](README.md) · [下一章：Responses](04-responses.md)

前两章已经用 FakeModel 跑通工具循环。这一章把假模型换成 HTTP 服务。循环仍然只读取 `Message`、`ToolCall` 和 `ModelReply`。

完成后，你能解释一个工具调用怎样跨过两次 HTTP 请求：第一次模型提出调用，本地执行，第二次模型看到结果并回答。验收时使用假 HTTP 响应，因此不用准备 API Key。

## 本章新增什么

| 文件 | 职责 |
| --- | --- |
| [providers/base.py](../lessons/stages/03/minicode/providers/base.py) | 配置、HTTP 传输、参数解析、原始数据的回放规则 |
| [providers/chat.py](../lessons/stages/03/minicode/providers/chat.py) | Chat Completions 请求编码与响应解码 |
| [providers/__init__.py](../lessons/stages/03/minicode/providers/__init__.py) | 根据明确的协议名选择适配器 |
| [03_chat_completions.py](../examples/03_chat_completions.py) | 完整的离线 HTTP 工具往返 |
| [test_ch03.py](../tests/test_ch03.py) | 请求格式和响应语义的契约测试 |

表中的链接都是本章可用的完整文件，不含未来的协议导入或流式方法。新增文件可按完整文件建立；已有文件按下面迁移步骤修改后，与本章版本逐项对照。后面的短代码块会明确标为替换代码或解释片段。

## 从阶段快照提取到正式模块

前两章的代码分别保存在 [lessons/ch01.py](../lessons/ch01.py) 和 [lessons/ch02.py](../lessons/ch02.py)。它们是那个阶段的完整快照：第一章只依赖标准库，第二章再增加工具注册和参数校验。现在要接入真实 HTTP，需要先把它们拆成正式模块。

保留这两份快照及其测试，按下面的顺序搬代码；不要直接让正式模块 import `lessons`：

| 原有代码 | 搬到哪里 | 本章需要的变化 |
| --- | --- | --- |
| ch01 的 Text、ToolCall、ToolResult、Message、ToolSpec、ModelReply 与两个取内容函数 | [types.py](../lessons/stages/03/minicode/types.py) | 补上下面的错误、用量和回放字段 |
| ch01 的 FakeModel、call 与调用 ID 计数器 | [testing.py](../lessons/stages/03/minicode/testing.py) | 改为从 `.types` 导入类型，返回含 Usage 的回复 |
| ch02 的 ToolContext、Tool、ToolRunner | [tool.py](../lessons/stages/03/minicode/tool.py) | 改用 `.types`，在 schema 校验前处理参数解析错误 |
| ch02 的 Agent | [agent.py](../lessons/stages/03/minicode/agent.py) | 改用 `.types`，累计用量，在终结事件里报告错误原因 |
| 新增的模型接口声明 | [model.py](../lessons/stages/03/minicode/model.py) | 此时只声明 `complete()` |

新建空的 `minicode/__init__.py`，使目录成为 Python 包。如果你直接运行仓库的参考实现，这些文件已经存在；手写时按照上表逐个建立。

### 先补上真实请求才需要的数据

`Text`、`ToolResult` 和 `ToolSpec` 的业务含义不变。以下字段是本章相对阶段快照的新增内容：

```python
# types.py 已从 dataclasses 导入 dataclass、field
@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    raw_arguments: str = ""
    parse_error: str = ""
    type: str = field(default="tool_call", init=False)


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelReply:
    message: Message
    usage: Usage
    stop_reason: str
    error: str = ""
```

同时在已有 `Message` 的 `content` 后新增 `provider_data: dict = field(default_factory=dict)`，保留 `Message.text()`。这个字段由适配器保存无法放进公共内容块的续轮数据，Agent 不解析它。

`raw_arguments` 保留服务商返回的参数原文；`parse_error` 说明为什么它无法解析为对象。内部的正常参数仍然是 `arguments` 对象。`Usage` 记录实际请求用量，FakeModel 可以返回零。`ModelReply.error` 保存非正常结束时可展示的原因。

注意 `ModelReply` 的构造方式已经改变。阶段快照使用 `ModelReply(message, stop_reason)`，正式类型中间增加了 `usage`。迁移 FakeModel 和测试时使用具名参数，避免把 `"stop"` 填进用量字段：

```python
from minicode.types import Message, ModelReply, Usage

reply = ModelReply(
    message=Message.text("assistant", "完成"),
    usage=Usage(input_tokens=0, output_tokens=0),
    stop_reason="stop",
)
```

FakeModel 返回工具调用时，也使用同样的具名构造，只把 `message` 换成包含 `ToolCall` 的 assistant 消息，把 `stop_reason` 换成 `"tool_calls"`。

### 再迁移执行层和循环

`ToolRunner.run(self, request)` 保留第二章的参数名 `request`。把原来的“参数必须是对象”分支完整替换为下面这段；它仍放在查到工具之后、schema 校验之前：

```python
if request.parse_error or not isinstance(request.arguments, dict):
    return ToolResult(
        call_id=request.id,
        content=f"参数不是合法 JSON 对象：{request.parse_error or request.raw_arguments}",
        is_error=True,
    )
```

在 Agent 初始化中增加 `self.usage = Usage()`。每次得到完整回复，累加 `reply.usage.input_tokens` 和 `output_tokens`；终结事件把用量转成字典。遇到非正常停止状态时显示 `reply.error`，没有专门原因时再显示回复文本。工具调用与结果的配对方式保持第 2 章的规则。

最后新建 `model.py`，用 Protocol 写下这个边界：

```python
from typing import Protocol
from .types import Message, ModelReply, ToolSpec


class ModelClient(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolSpec] = ()) -> ModelReply: ...
```

Protocol 描述依赖的能力，不要求 FakeModel 或适配器继承它。它们都能提供 `complete()` 即可。

从本章开始，示例统一使用 `minicode.types`、`minicode.testing`、`minicode.tool` 和 `minicode.agent`。**不要混用 `lessons.ch01.Message` 与 `minicode.types.Message`**：它们是不同的 Python 类，正式执行层的类型检查会拒绝阶段快照对象。

本章阶段目录只包含非流式 `complete()`：工厂只注册 Chat Completions，`demo.py` 调用 `Agent(model, runner)`，没有 `StreamEvent`、`stream` 构造参数或消息序列化函数。第 04、05 章才扩展工厂，第 08 章加入流式，第 10 章加入持久化。不要用仓库根目录的最终文件替代本章阶段版本。

迁移完成后，先用正式 FakeModel 再跑一次“加法调用 → 回答”，再接下面的 HTTP 适配器：

```bash
uv run python - <<'PYCODE'
from pathlib import Path
from minicode.agent import Agent
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec

spec = ToolSpec("add", "两个整数相加", {
    "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"], "additionalProperties": False,
})
runner = ToolRunner([Tool(spec, lambda args, ctx: str(args["a"] + args["b"]))], ToolContext(Path.cwd()))
agent = Agent(FakeModel([call("add", a=2, b=3), "5"]), runner)
result = list(agent.run("2 + 3 是多少？"))[-1]
assert result["status"] == "success" and result["text"] == "5"
print(result["text"], result["usage"])
PYCODE
```

应得到 `5 {'input_tokens': 0, 'output_tokens': 0}`。如果这一步失败，先解决模块迁移问题，再检查外部协议映射。

## 第一步：把“协议”与“服务商”分开

一个服务商可以暴露多个协议，多个服务商也可以实现同一个协议。`api` 决定如何组织 HTTP 数据；`provider` 标记这条路由属于谁。

```python
from minicode.providers import ModelConfig

config = ModelConfig(
    api="openai-chat",
    provider="openai",
    model="你的账号可用的模型",
    base_url="https://api.openai.com/v1",
    api_key="从环境变量读取",
)
```

`base_url` 包含版本前缀，适配器再添加 `/chat/completions`。不要把完整 endpoint 填进 `base_url`，否则路径会重复。

模型名称必须显式配置。不能因为换了 URL，就假定服务商支持相同模型、参数和工具能力。`ModelConfig.chat_token_field` 控制输出上限字段：默认 OpenAI 使用 `max_completion_tokens`，DeepSeek 路由使用 `max_tokens`；其他兼容服务可以显式选择。它只影响协议请求，Agent 无需增加服务商判断。

这是必须验证的行为：一个服务可能接受未知字段，却忽略它。测试应检查实际发出的字段；真实验收还应把上限设成很小的值，确认用量和停止原因符合限制。

## 第二步：编码内部消息

回顾内部消息中的一个调用：

```python
from minicode.types import Message, Text, ToolCall, ToolResult

messages = [
    Message.text("user", "2 加 3 等于多少？"),
    Message("assistant", [ToolCall("call_1", "add", {"a": 2, "b": 3})]),
    Message("tool", [ToolResult("call_1", "5")]),
]
```

`ChatCompletionsModel.encode()` 把它变成：

```json
[
  {"role": "user", "content": "2 加 3 等于多少？"},
  {"role": "assistant", "content": "", "tool_calls": [
    {"id": "call_1", "type": "function", "function": {
      "name": "add", "arguments": "{\"a\": 2, \"b\": 3}"
    }}
  ]},
  {"role": "tool", "tool_call_id": "call_1", "content": "5"}
]
```

注意两次转换：内部参数对象被编码成 JSON 字符串，内部工具结果被编码成独立的 tool 消息。这些操作都属于协议边界。

工具声明也由适配器包装。`ToolSpec.parameters` 保持普通 JSON Schema，`encode()` 才加上 `type` 和 `function`：

```python
wire_tool = {
    "type": "function",
    "function": {
        "name": spec.name,
        "description": spec.description,
        "parameters": spec.parameters,
        "strict": False,
    },
}
```

这里显式使用非严格模式，保持第 2 章 schema 中可选参数的含义。本地 `ToolRunner` 仍然在执行前校验参数。服务端生成约束和本地执行校验解决的是不同阶段的问题。

## 第三步：解码完整回复

HTTP 返回的 `choices[0].message` 不能直接交给 Agent。`decode()` 要完成四件事：

1. 将可见文本变成 `Text`。
2. 将每个 `tool_calls` 元素变成 `ToolCall`。
3. 将 token 用量变成 `Usage`。
4. 将停止原因变成内部状态。

| Chat Completions 字段 | 内部状态 |
| --- | --- |
| `finish_reason="stop"` | `stop` |
| `finish_reason="tool_calls"` | `tool_calls` |
| `finish_reason="length"` | `length` |
| `content_filter` 或 refusal 内容 | `refusal` |
| 未识别的结束状态 | `error` |

遇到无效 JSON，`parse_call()` 保留原文、写入 `parse_error`，不猜测、不自动补括号。`ToolRunner` 会生成带调用 ID 的错误结果，让模型有机会修正。

`length` 则不同：模型这一轮尚未完成，即使已有看起来合法的调用，也不能执行。第 2 章的 Agent 会返回 `length`，而不会报告成功。

## 第四步：保留必要的原始数据

一些兼容服务要求续轮回传 `reasoning_content`。我们把允许回放的原始 assistant 字段放进 `Message.provider_data`，并同时记录来源和内部内容的指纹。

只有来源一致、内部内容未修改时才原样回放。修改消息或切换模型后，适配器重新编码内部内容，不会用旧的原始回复覆盖编辑结果，也不会把另一家服务的推理数据盲传过去。

这不代表任意模型之间可以无损续接推理。当前教程保证共同的文本和工具语义；专有数据只在原路由中保留。

## 跑一次完整往返

在本章练习目录运行：

```bash
uv run python examples/03_chat_completions.py
```

预期输出：

```text
openai-chat: 两次 HTTP 请求，一次本地工具调用
结果是 5。
```

打开 [demo.py](../lessons/stages/03/minicode/providers/demo.py)，沿着 `run_demo()` 看一次完整过程：

1. `httpx.MockTransport` 截获请求，代替真实网络。
2. 第一次返回 `add` 调用。
3. 相同的 `ToolRunner` 执行加法。
4. 第二次请求包含工具结果。
5. 假服务返回最终文本，Agent 输出 `success`。

这里替换的是 HTTP 传输。请求编码、响应解码和 Agent 循环都运行了真实实现，所以能发现仅用 FakeModel 测不到的字段错误。

## 检查与练习

```bash
uv run python -m pytest tests/test_ch03.py -q
```

本章测试只导入当前已有模块，检查 Chat 工具往返及内部消息转换。读测试中的第二次请求，确认包含原调用和匹配 ID 的结果。认证失败直接抛出 HTTP 错误，再由 Agent 产生错误事件；当前传输层没有自动重试。

练习：把假服务第一次的 `finish_reason` 改成 `length`。确认加法函数没有执行，再恢复正常响应。接着把 `arguments` 改成 `"{"`，观察它与截断的区别。

## 本章就接一次真实模型

完整入口是 [live_model.py](../examples/live_model.py)，本章导出目录已经包含它。它仍然使用同一个加法工具，只把 MockTransport 换成真实 HTTP；不依赖第 16 章 CLI。

如果终端已有 `LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY`，直接运行：

```bash
uv run python examples/live_model.py --env-prefix LLM --api openai-chat
```

如果使用教程配置，则准备同一组 `MINICODE_MODEL`、`MINICODE_BASE_URL`、`MINICODE_API_KEY`，把参数改为 `--env-prefix MINICODE`。入口要求选中的一组配置完整，不会把一家的 key 和另一家的 URL 拼在一起。密钥只从环境变量读取，不打印到终端。

对于 `api.deepseek.com`，入口识别 DeepSeek 路由并选择 `max_tokens`。其他兼容服务需要不同字段时，可以显式加 `--chat-token-field max_tokens`。不要只以“请求返回 200”判断输出上限生效。

模型应调用加法工具，再给出包含 5 的回答。程序检查实际发生了工具调用和最终状态，模型没有调用工具也会作为验收失败报告。真实请求会产生服务商费用；离线例子不请求服务。

当前只实现文本与本地 function 工具，未实现图像、音频和服务商托管工具。第 16 章才把已经验证的这些模块组装成完整 CLI。

源码参照：[pi 的 Chat 转换](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/api/openai-completions.ts)将统一消息转换为协议消息；本章保留这一边界，并缩小支持范围。

协议资料：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。
