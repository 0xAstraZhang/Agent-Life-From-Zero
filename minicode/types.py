"""模型、循环和工具共同使用的数据；这里没有任何服务商 SDK 类型。"""
from dataclasses import asdict, dataclass, field
from typing import Literal


@dataclass
class Text:
    text: str
    type: Literal["text"] = field(default="text", init=False)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict | None
    raw_arguments: str = ""
    parse_error: str = ""
    type: Literal["tool_call"] = field(default="tool_call", init=False)


@dataclass
class ToolResult:
    call_id: str
    content: str
    is_error: bool = False
    type: Literal["tool_result"] = field(default="tool_result", init=False)


@dataclass
class Message:
    role: str
    content: list[Text | ToolCall | ToolResult]
    provider_data: dict = field(default_factory=dict)

    @classmethod
    def text(cls, role: str, text: str) -> "Message":
        return cls(role, [Text(text)])


def message_text(message: Message) -> str:
    return "".join(part.text for part in message.content if isinstance(part, Text))


def tool_calls(message: Message) -> list[ToolCall]:
    return [part for part in message.content if isinstance(part, ToolCall)]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict


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
    usage_details: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


@dataclass
class StreamEvent:
    type: str
    text: str = ""
    call_id: str = ""
    arguments_delta: str = ""
    reply: ModelReply | None = None


def message_to_dict(message: Message) -> dict:
    """落盘保存的是内部消息，provider_data 作为不透明的续轮数据一同保存。"""
    return asdict(message)


def message_from_dict(data: dict) -> Message:
    """读取会话时先检查形状，避免损坏的记录进入 Agent。"""
    if not isinstance(data, dict):
        raise ValueError("message 必须是对象")
    role = data.get("role")
    if role not in {"system", "user", "assistant", "tool"}:
        raise ValueError(f"未知消息角色：{role!r}")
    if not isinstance(data.get("content"), list):
        raise ValueError("message.content 必须是列表")
    metadata = data.get("provider_data", {})
    if not isinstance(metadata, dict):
        raise ValueError("message.provider_data 必须是对象")
    parts = []
    for part in data["content"]:
        if not isinstance(part, dict):
            raise ValueError("消息内容块必须是对象")
        kind = part.get("type")
        if kind == "text" and role != "tool" and isinstance(part.get("text"), str):
            parts.append(Text(part["text"]))
        elif kind == "tool_call" and role == "assistant":
            if not all(isinstance(part.get(key), str) and part[key] for key in ("id", "name")):
                raise ValueError("工具调用必须有非空 id 和 name")
            args = part.get("arguments")
            raw, error = part.get("raw_arguments", ""), part.get("parse_error", "")
            if args is not None and not isinstance(args, dict):
                raise ValueError("工具参数必须是对象或 None")
            if not isinstance(raw, str) or not isinstance(error, str):
                raise ValueError("工具原始参数和解析错误必须是字符串")
            parts.append(ToolCall(part["id"], part["name"], args, raw, error))
        elif kind == "tool_result" and role == "tool":
            if not isinstance(part.get("call_id"), str) or not part["call_id"]:
                raise ValueError("工具结果必须有非空 call_id")
            if not isinstance(part.get("content"), str) or not isinstance(part.get("is_error", False), bool):
                raise ValueError("工具结果需要字符串 content 和布尔 is_error")
            parts.append(ToolResult(part["call_id"], part["content"], part.get("is_error", False)))
        else:
            raise ValueError(f"{role} 消息中的内容块不合法：{kind!r}")
    return Message(role, parts, metadata)
