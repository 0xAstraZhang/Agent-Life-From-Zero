"""Anthropic Messages: tool uses and tool results are content blocks."""
import copy
import json

from ..types import Message, ModelReply, StreamEvent, Text, ToolCall, ToolResult, Usage, message_text
from .base import HTTPModel, ProtocolError


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
                              "input_schema": self.tool_schema(t.parameters), **({"strict": True} if self.config.strict_tools else {})} for t in tools]
        body.update(self.controls(tools))
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
                content.append(self.parse_call(block.get("id"), block.get("name"), block.get("input")))
            elif kind not in ("thinking", "redacted_thinking"):
                raise ProtocolError(f"不支持的 Anthropic 内容块：{kind}")
        reason = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
                  "max_tokens": "length", "refusal": "refusal"}.get(response.get("stop_reason"), "error")
        u = response.get("usage") or {}
        input_tokens = sum(u.get(k, 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        message = self.remember(Message("assistant", content), response.get("content", []))
        return self.finish(ModelReply(message, Usage(input_tokens, u.get("output_tokens", 0)), reason,
                          "不支持的 stop_reason" if reason == "error" else ""), response)

    def _stream(self, messages, tools=None):
        response = None
        blocks = {}
        inputs = {}
        open_blocks = set()
        for event in self._events(messages, tools):
            if event == "[DONE]":
                continue
            kind = event.get("type")
            if kind == "message_start":
                response = copy.deepcopy(event["message"])
            elif kind == "content_block_start":
                index = event["index"]
                blocks[index] = copy.deepcopy(event["content_block"])
                open_blocks.add(index)
            elif kind == "content_block_delta":
                index, delta = event["index"], event["delta"]
                if index not in open_blocks:
                    raise ProtocolError("content delta 没有对应 start")
                block = blocks[index]
                if delta["type"] == "text_delta":
                    block["text"] = block.get("text", "") + delta["text"]
                    yield StreamEvent("text_delta", text=delta["text"])
                elif delta["type"] == "input_json_delta":
                    inputs[index] = inputs.get(index, "") + delta["partial_json"]
                    yield StreamEvent("tool_call_delta", call_id=block["id"], arguments_delta=delta["partial_json"])
                elif delta["type"] == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta["thinking"]
                elif delta["type"] == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta["signature"]
                else:
                    raise ProtocolError(f"不支持的 Anthropic delta：{delta['type']}")
            elif kind == "content_block_stop":
                index = event["index"]
                open_blocks.discard(index)
            elif kind == "message_delta":
                if response is None:
                    raise ProtocolError("message_delta 没有 message_start")
                response.update(event.get("delta", {}))
                response.setdefault("usage", {}).update(event.get("usage", {}))
            elif kind == "message_stop":
                if response is None or open_blocks:
                    raise ProtocolError("Anthropic 流内容块未完整结束")
                # A truncated tool block can stop before its JSON is complete.
                # Wait for message_delta to learn max_tokens and retain usage.
                malformed = False
                for index, raw in inputs.items():
                    try:
                        blocks[index]["input"] = json.loads(raw)
                    except ValueError:
                        blocks[index]["input"] = raw
                        malformed = True
                response["content"] = [blocks[i] for i in sorted(blocks)]
                reply = self.decode(response)
                if malformed and reply.stop_reason == "tool_calls":
                    reply.stop_reason = "error"
                    reply.error = "工具参数流不是完整 JSON"
                yield StreamEvent("done", reply=reply)
                return
        raise ProtocolError("Anthropic 流缺少 message_stop")
