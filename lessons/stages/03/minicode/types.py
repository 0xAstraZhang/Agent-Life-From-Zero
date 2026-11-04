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
