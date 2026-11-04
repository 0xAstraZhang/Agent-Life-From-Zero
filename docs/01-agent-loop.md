# 第 1 章：从一个空文件写出 Agent Loop

上一章：[准备环境](00-setup.md) · 下一章：[统一工具系统](02-tools.md)

本章从一个空 Python 文件开始：先定义消息，再写假模型，最后让程序读一个文件并回答问题。
完成后得到一份只依赖标准库的程序；没有框架、SDK、权限系统或未来章节的空接口。

完整快照是 [`lessons/ch01.py`](../lessons/ch01.py)，[`examples/01_agent_loop.py`](../examples/01_agent_loop.py) 只是它的运行入口。
可以在自己的练习目录新建 `agent.py`，按下面四步顺序写入代码。每个代码块都接在前一个后面，拼起来就是可运行程序。

## 1. 定义内部消息

一次任务的过程是：

```text
用户提问 → 模型请求读文件 → Python 执行 → 模型收到文件内容 → 模型回答
```

模型提出动作，Python 执行动作。因此历史既要记录“调用”，也要记录“结果”。
先写导入和数据类型：

```python
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory


@dataclass
class Text:
    text: str
    type: str = field(default="text", init=False)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    type: str = field(default="tool_call", init=False)


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False
    type: str = field(default="tool_result", init=False)


@dataclass
class Message:
    role: str
    content: list[Text | ToolCall | ToolResult]

    @classmethod
    def text(cls, role, text):
        return cls(role, [Text(text)])


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


@dataclass
class ModelReply:
    message: Message
    stop_reason: str


def message_text(message):
    return "".join(part.text for part in message.content if part.type == "text")


def tool_calls(message):
    return [part for part in message.content if part.type == "tool_call"]


```

这些类是 minicode 的内部语言，不是服务商的 HTTP 格式：

| 类型 | 它表达什么 |
| --- | --- |
| `Text` | 用户或助手的文本 |
| `ToolCall` | 调用 ID、工具名称和参数对象 |
| `ToolResult` | 对应调用 ID 的结果与错误状态 |
| `Message` | 谁说的，以及按顺序出现的内容块 |
| `ToolSpec` | 给模型看的工具说明和参数要求 |
| `ModelReply` | 本轮完整消息，以及模型为何停止 |

一次可以调用同名工具两次，所以结果必须按 ID 配对，不能只按名称配对。
`content` 使用列表，是为了表达“解释文字之后紧跟一个调用”等顺序。
`role="tool"` 是内部角色；第五章的 Anthropic 适配器会将它编码成 user 消息中的结果块。

本章只需要消息和停止原因。真实接口加入时，再给 `ModelReply` 增加 `usage`，给 `Message` 增加保留原始续轮数据的 `provider_data`。

## 2. 写一个按剧本回答的模型

先固定模型的选择，才能判断循环有没有写对。继续在文件中加入：

```python
_ids = count(1)


def call(name, /, **args):
    return ToolCall(f"fake_{next(_ids)}", name, args)


class FakeModel:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def complete(self, messages, tools=()):
        self.requests.append(deepcopy({"messages": messages, "tools": list(tools)}))
        if not self.script:
            raise RuntimeError("剧本已用完")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, ModelReply):
            return deepcopy(step)
        if isinstance(step, str):
            return ModelReply(Message.text("assistant", step), "stop")
        calls = [step] if isinstance(step, ToolCall) else step
        if not isinstance(calls, list) or not calls or not all(isinstance(c, ToolCall) for c in calls):
            raise TypeError("剧本项必须是文本、工具调用、回复或异常")
        return ModelReply(Message("assistant", deepcopy(calls)), "tool_calls")


```

剧本中的字符串代表最终回答，`call(...)` 代表一次调用，列表代表同一轮的多个调用。
`complete()` 每次取出一个剧本项，转换成刚才定义的内部回复；它完全不知道 Chat Completions、Responses 或 Anthropic 的字段。

`requests` 保存请求的深拷贝，测试可以检查第二次请求是否真的包含工具结果。
剧本耗尽就抛错，让漏掉的模型步骤及时暴露。

可以先在交互环境试一下：

```python
model = FakeModel([call("read_file", path="notes.txt"), "样本 B 是 1.37"])
first = model.complete([Message.text("user", "读记录")])
assert first.stop_reason == "tool_calls"
assert tool_calls(first.message)[0].arguments == {"path": "notes.txt"}
```

这是单独的观察练习，不要把这几行插进最终演示的剧本中；它会消费第一次回复。

## 3. 写出循环和唯一的工具分支

继续写入完整 `run()`：

```python
def run(model, prompt: str, root: Path, max_turns=3):
    messages = [Message.text("system", "先读文件，再回答用户。"), Message.text("user", prompt)]
    spec = ToolSpec("read_file", "读取工作目录中的一个 UTF-8 文件", {
        "type": "object", "properties": {"path": {"type": "string"}},
        "required": ["path"], "additionalProperties": False,
    })
    for turn in range(1, max_turns + 1):
        try:
            reply = model.complete(messages, [spec])
        except Exception as error:
            yield {"type": "result", "status": "error", "text": str(error)}
            return
        if reply.stop_reason not in {"stop", "tool_calls"}:
            yield {"type": "result", "status": reply.stop_reason, "text": message_text(reply.message)}
            return
        calls = tool_calls(reply.message)
        if bool(calls) != (reply.stop_reason == "tool_calls"):
            yield {"type": "result", "status": "error", "text": "停止原因与调用不一致"}
            return
        messages.append(reply.message)
        if reply.stop_reason == "stop":
            yield {"type": "result", "status": "success", "text": message_text(reply.message), "turns": turn}
            return
        for request in calls:
            try:
                if request.name != "read_file":
                    raise ValueError(f"未知工具：{request.name}")
                if not isinstance(request.arguments, dict):
                    raise ValueError("工具参数不是合法对象")
                path = (root / request.arguments["path"]).resolve()
                if not path.is_relative_to(root.resolve()):
                    raise ValueError("文件必须在示例工作目录中")
                result = ToolResult(request.id, path.read_text(encoding="utf-8"))
            except Exception as error:
                result = ToolResult(request.id, f"{type(error).__name__}: {error}", True)
            messages.append(Message("tool", [result]))
            yield {"type": "tool_result", "call_id": result.call_id, "content": result.content,
                   "is_error": result.is_error}
    yield {"type": "result", "status": "max_turns", "text": "本轮任务已达到模型调用上限"}

```

按数据流逐段理解它：

1. 创建 system 和 user 消息。
2. 把历史与工具说明交给模型。
3. 先检查停止原因。`length` 是截断，不能作为成功，更不能执行里面的半成品调用。
4. 保存正常的助手消息。正常结束就返回回答；要求工具就逐个执行。
5. 每次调用都追加匹配 ID 的结果，失败也不能漏掉。
6. 进入下一轮模型请求，或者达到轮数上限。

路径检查让这个示例只读取传入工作目录中的文件。文件不存在、参数缺失和未知工具都会变成错误结果交还模型。
模型请求失败则终止任务：工具层不能替模型修复认证失败或断网。

一次模型请求叫一轮。最后一轮执行过工具，也不等于已经取得最终回答；还需要下一轮解释结果时，轮数上限会返回 `max_turns`。

## 4. 加入入口并运行

最后写入：

```python
def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "notes.txt").write_text("样本 B = 1.37", encoding="utf-8")
        model = FakeModel([call("read_file", path="notes.txt"), "样本 B 的值是 1.37。"])
        for event in run(model, "样本 B 的值是多少？", root):
            print(event)


if __name__ == "__main__":
    main()
```

完整的练习文件直接用 Python 运行即可。运行仓库里的快照则用：

```bash
uv run python lessons/ch01.py
# 统一示例入口也运行同一份快照：
uv run python examples/01_agent_loop.py
```

程序在临时目录写入记录，结束时自动清理。输出包含工具结果和最终回答，调用 ID 的数字可能不同：

```text
{'type': 'tool_result', 'call_id': 'fake_1', 'content': '样本 B = 1.37', 'is_error': False}
{'type': 'result', 'status': 'success', 'text': '样本 B 的值是 1.37。', 'turns': 2}
```

第二次请求的历史依次为 `system → user → assistant(ToolCall) → tool(ToolResult)`。
返回工具结果只代表一个动作完成；最终回答要由下一次模型回复给出。

## 5. 验收出口

```bash
uv run pytest tests/test_lessons.py -k 'stage or standalone' -q
```

测试用同一套场景检查第一、二章快照：工具往返、未知工具、截断、模型异常和轮数上限。
另外会把第一章源码复制到临时目录，在 Python 隔离模式下运行，证明它不需要最终 `minicode` 包。

再修改剧本，观察结果：

| 修改 | 预期 |
| --- | --- |
| 第一项就是文本 | 一轮结束，不执行工具 |
| 请求不存在的文件 | 错误结果进入下一轮模型请求 |
| 第一项是 `ConnectionError("断网")` | 返回 `error` |
| `max_turns=1`，剧本先读文件 | 工具执行后返回 `max_turns` |

练习：增加只读的 `list_files` 分支，写出“列目录 → 读文件 → 回答”的剧本。
这会直接暴露下一章要解决的问题：每增加一个工具，都必须修改 `run()`。

本章消息分层可对照 [pi 的统一类型](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/types.ts)阅读。后面从当前需要出发逐步增加字段。

下一章：[把工具从循环中拆出来](02-tools.md)。
