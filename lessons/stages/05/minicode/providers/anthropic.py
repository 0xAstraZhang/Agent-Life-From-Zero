"""Anthropic Messages: tool uses and tool results are content blocks."""
import copy
import json

from ..types import Message, ModelReply, Text, ToolCall, ToolResult, Usage, message_text
from .base import HTTPModel, ProtocolError, parse_call


class AnthropicModel(HTTPModel):
    endpoint = "/messages"

    def headers(self):
        return {"x-api-key": self.config.api_key, "anthropic-version": "2023-06-01"}

    def encode(self, messages, tools):
        system = []
        wire = []
        for message in messages:
            if message.role == "system":
                system.append(message_text(message))
                continue
            role = "user" if message.role == "tool" else message.role
            content = self.replay(message) if message.role == "assistant" else None
            if content is None:
                content = []
                for part in message.content:
                    if isinstance(part, Text) and part.text:
                        content.append({"type": "text", "text": part.text})
                    elif isinstance(part, ToolCall):
                        if part.arguments is None:
                            raise ProtocolError("无效工具参数不能转换为 Anthropic input 对象")
                        content.append({"type": "tool_use", "id": part.id, "name": part.name,
                                        "input": part.arguments})
                    elif isinstance(part, ToolResult):
                        content.append({"type": "tool_result", "tool_use_id": part.call_id,
                                        "content": part.content, "is_error": part.is_error})
            if not content:
                continue
            if wire and wire[-1]["role"] == role:
                # In particular, a batch of local tool messages is one user turn.
                wire[-1]["content"].extend(content)
            else:
                wire.append({"role": role, "content": content})
        body = {"model": self.config.model, "max_tokens": self.config.max_tokens, "messages": wire}
        if system:
            body["system"] = "\n\n".join(system)
        if tools:
            body["tools"] = [{"name": t.name, "description": t.description,
                              "input_schema": t.parameters} for t in tools]
        return body

    def decode(self, response):
        if response.get("type") == "error":
            raise ProtocolError("Anthropic 返回错误")
        content = []
        for block in response.get("content", []):
            kind = block.get("type")
            if kind == "text":
                content.append(Text(block["text"]))
            elif kind == "tool_use":
                content.append(parse_call(block.get("id"), block.get("name"), block.get("input")))
            elif kind not in ("thinking", "redacted_thinking"):
                raise ProtocolError(f"不支持的 Anthropic 内容块：{kind}")
        reason = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
                  "max_tokens": "length", "refusal": "refusal"}.get(response.get("stop_reason"), "error")
        u = response.get("usage") or {}
        input_tokens = sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        message = self.remember(Message("assistant", content), response.get("content", []))
        return ModelReply(message, Usage(input_tokens, u.get("output_tokens", 0)), reason,
                          "不支持的 stop_reason" if reason == "error" else "")
