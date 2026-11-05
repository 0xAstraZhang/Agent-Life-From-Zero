"""OpenAI Responses: messages and tool exchanges are typed input/output items."""
from ..types import Message, ModelReply, StreamEvent, Text, ToolCall, ToolResult, Usage, message_text
from .base import HTTPModel, ProtocolError, arguments, parse_call


class ResponsesModel(HTTPModel):
    endpoint = "/responses"

    def encode(self, messages, tools):
        items = []
        for message in messages:
            saved = self.replay(message) if message.role == "assistant" else None
            if saved is not None:
                items.extend(saved)
                continue
            for part in message.content:
                if isinstance(part, Text):
                    items.append({"role": message.role, "content": part.text})
                elif isinstance(part, ToolCall):
                    items.append({"type": "function_call", "call_id": part.id,
                                  "name": part.name, "arguments": arguments(part)})
                elif isinstance(part, ToolResult):
                    items.append({"type": "function_call_output", "call_id": part.call_id,
                                  "output": f"[tool error] {part.content}" if part.is_error else part.content})
        body = {"model": self.config.model, "input": items, "store": False,
                "include": ["reasoning.encrypted_content"], "max_output_tokens": self.config.max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                              "parameters": t.parameters, "strict": False} for t in tools]
        return body

    def decode(self, response):
        content = []
        refused = False
        for item in response.get("output", []):
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content", []):
                    if part["type"] == "output_text":
                        content.append(Text(part["text"]))
                    elif part["type"] == "refusal":
                        refused = True
                        content.append(Text(part["refusal"]))
                    else:
                        raise ProtocolError(f"不支持的 Responses 内容：{part['type']}")
            elif kind == "function_call":
                content.append(parse_call(item.get("call_id"), item.get("name"), item.get("arguments", "")))
            elif kind != "reasoning":
                raise ProtocolError(f"不支持的 Responses 输出项：{kind}")
        status = response.get("status")
        error = ""
        if status == "completed":
            reason = "tool_calls" if any(isinstance(p, ToolCall) for p in content) else "stop"
        elif status == "incomplete":
            why = (response.get("incomplete_details") or {}).get("reason")
            reason = {"max_output_tokens": "length", "content_filter": "refusal"}.get(why, "error")
            error = why or "response incomplete"
        elif status == "cancelled":
            reason = "cancelled"
        else:
            reason, error = "error", "Responses 没有正常完成"
        if refused:
            reason = "refusal"
        u = response.get("usage") or {}
        message = self.remember(Message("assistant", content), response.get("output", []))
        return ModelReply(message, Usage(u.get("input_tokens", 0), u.get("output_tokens", 0)), reason, error)

    def stream(self, messages, tools=None):
        call_ids = {}
        for event in self._events(messages, tools):
            if event == "[DONE]":
                continue
            kind = event.get("type")
            if kind == "response.output_item.added" and event["item"].get("type") == "function_call":
                call_ids[event["item"]["id"]] = event["item"]["call_id"]
            elif kind == "response.output_text.delta":
                yield StreamEvent("text_delta", text=event["delta"])
            elif kind == "response.function_call_arguments.delta":
                # item_id is only a stream assembly key, not a tool result call_id.
                yield StreamEvent("tool_call_delta", call_id=call_ids.get(event["item_id"], ""), arguments_delta=event["delta"])
            elif kind in ("response.completed", "response.incomplete", "response.failed"):
                # The terminal response contains the authoritative complete items,
                # including reasoning and call_id; never execute from deltas.
                yield StreamEvent("done", reply=self.decode(event["response"]))
                return
        raise ProtocolError("Responses 流缺少终止事件")
