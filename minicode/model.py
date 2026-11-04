"""模型边界：适配器处理协议，Agent 处理循环。"""
from collections.abc import Iterator
from typing import Protocol

from .types import Message, ModelReply, StreamEvent, ToolSpec


class ModelClient(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolSpec] = ()) -> ModelReply: ...

    def stream(self, messages: list[Message], tools: list[ToolSpec] = ()) -> Iterator[StreamEvent]: ...
