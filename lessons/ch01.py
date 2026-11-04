"""第 1 章完整快照：只依赖 Python 标准库，可从空文件逐段手写。"""
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from tempfile import TemporaryDirectory


# 第一步：定义内部消息。它们不是任何服务商的 HTTP 请求对象。
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


# 第二步：先固定模型选择，单独观察程序的控制流。
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


# 第三步：一个循环 + 一个消息列表 + 一个工具分支。
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


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "notes.txt").write_text("样本 B = 1.37", encoding="utf-8")
        model = FakeModel([call("read_file", path="notes.txt"), "样本 B 的值是 1.37。"])
        for event in run(model, "样本 B 的值是多少？", root):
            print(event)


if __name__ == "__main__":
    main()
