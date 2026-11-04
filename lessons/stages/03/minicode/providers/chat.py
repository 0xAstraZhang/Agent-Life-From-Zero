"""OpenAI Chat Completions wire adapter (text and local function tools)."""
from ..types import Message, ModelReply, Text, ToolCall, ToolResult, Usage, message_text
from .base import HTTPModel, ProtocolError, arguments, parse_call


class ChatCompletionsModel(HTTPModel):
    endpoint = "/chat/completions"

    def encode(self, messages, tools):
        wire = []
        for message in messages:
            if message.role == "tool":
                for part in message.content:
                    if isinstance(part, ToolResult):
                        content = (f"[tool error] {part.content}" if part.is_error else part.content)
                        wire.append({"role": "tool", "tool_call_id": part.call_id, "content": content})
                continue
            saved = self.replay(message) if message.role == "assistant" else None
            if saved is not None:
                wire.append(saved)
                continue
            value = {"role": message.role, "content": message_text(message)}
            calls = [p for p in message.content if isinstance(p, ToolCall)]
            if calls:
                value["tool_calls"] = [{"id": p.id, "type": "function", "function": {
                    "name": p.name, "arguments": arguments(p)}} for p in calls]
            wire.append(value)
        body = {"model": self.config.model, "messages": wire,
                self.config.chat_token_parameter: self.config.max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.parameters,
                "strict": False}} for t in tools]
        return body

    def decode(self, response):
        if response.get("error"):
            raise ProtocolError("Chat Completions 返回错误")
        choices = response.get("choices") or []
        if not choices:
            raise ProtocolError("Chat Completions 没有 choices")
        choice = choices[0]
        wire = choice["message"]
        content = []
        text = wire.get("content")
        if text is not None and not isinstance(text, str):
            raise ProtocolError("本教程只支持 Chat Completions 文本输出")
        if text:
            content.append(Text(text))
        if wire.get("refusal"):
            content.append(Text(wire["refusal"]))
        for call in wire.get("tool_calls") or []:
            if call.get("type") != "function":
                raise ProtocolError("不支持此类 Chat 工具")
            content.append(parse_call(call.get("id"), call["function"].get("name"),
                                      call["function"].get("arguments", "")))
        reason = {"stop": "stop", "tool_calls": "tool_calls", "length": "length",
                  "content_filter": "refusal"}.get(choice.get("finish_reason"), "error")
        if wire.get("refusal"):
            reason = "refusal"
        usage = response.get("usage") or {}
        # Only fields accepted on replay; keep provider-specific reasoning_content.
        saved = {k: v for k, v in wire.items() if k in {
            "role", "content", "tool_calls", "refusal", "reasoning_content"}}
        message = self.remember(Message("assistant", content), saved)
        return ModelReply(message, Usage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
                          reason, "未知 finish_reason" if reason == "error" else "")
