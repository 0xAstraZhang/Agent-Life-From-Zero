"""第 03 章：只声明完整回复，不提前加入流式接口。"""
from typing import Protocol
from .types import Message, ModelReply, ToolSpec


class ModelClient(Protocol):
    def complete(self, messages: list[Message], tools: list[ToolSpec] = ()) -> ModelReply: ...
