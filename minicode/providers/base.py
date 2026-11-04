"""HTTP transport and replay rules shared by the three wire protocols."""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass
from typing import Iterable, Iterator

import httpx

from ..types import Message, ModelReply, StreamEvent, ToolCall, ToolSpec


@dataclass(frozen=True)
class ModelConfig:
    api: str
    model: str
    base_url: str
    api_key: str
    provider: str = "custom"
    timeout: float = 60
    max_tokens: int = 4096
    chat_token_field: str | None = None

    def __post_init__(self):
        if not self.model or not self.base_url or not self.api_key:
            raise ValueError("model、base_url 和 api_key 都必须设置")
        if self.timeout <= 0 or self.max_tokens <= 0:
            raise ValueError("timeout 和 max_tokens 必须大于 0")
        if self.chat_token_field not in {None, "max_tokens", "max_completion_tokens"}:
            raise ValueError("chat_token_field 只能是 max_tokens 或 max_completion_tokens")

    @property
    def chat_token_parameter(self) -> str:
        """协议相同不代表参数完全相同；服务商差异停在模型配置边界。"""
        if self.chat_token_field is not None:
            return self.chat_token_field
        return "max_tokens" if self.provider.lower() == "deepseek" else "max_completion_tokens"


class ProtocolError(ValueError):
    """The server did not return a supported, complete protocol response."""


def _reject_constant(value):
    raise ValueError(f"非标准 JSON 数值：{value}")


def parse_call(call_id: str, name: str, raw: str | dict) -> ToolCall:
    if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
        raise ProtocolError("工具调用缺少 id 或 name")
    encoded = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    try:
        args = json.loads(encoded, parse_constant=_reject_constant)
        if not isinstance(args, dict):
            raise ValueError("参数必须是 JSON 对象")
    except (ValueError, TypeError) as exc:
        return ToolCall(call_id, name, None, encoded, str(exc))
    return ToolCall(call_id, name, args, encoded)


def arguments(call: ToolCall) -> str:
    return (json.dumps(call.arguments, ensure_ascii=False)
            if call.arguments is not None else call.raw_arguments)


def fingerprint(message: Message) -> str:
    # Never include provider_data itself or credentials in the fingerprint.
    return json.dumps({"role": message.role, "content": [asdict(p) for p in message.content]},
                      sort_keys=True, ensure_ascii=False)


def sse_data(lines: Iterable[str]) -> Iterator[dict | str]:
    """SSE frames may have comments and multiple data lines; EOF is not completion."""
    data: list[str] = []
    for line in lines:
        if not line:
            if data:
                payload = "\n".join(data)
                data.clear()
                if payload == "[DONE]":
                    yield payload
                else:
                    value = json.loads(payload)
                    if not isinstance(value, dict):
                        raise ProtocolError("SSE data 必须是 JSON 对象")
                    yield value
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        raise ProtocolError("SSE 在一个事件结束前断开")


class HTTPModel:
    endpoint = ""

    def __init__(self, config: ModelConfig, client: httpx.Client | None = None):
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout)
        self._owns_client = client is None

    def close(self):
        if self._owns_client:
            self.client.close()

    @property
    def source(self) -> dict:
        c = self.config
        return {"api": c.api, "provider": c.provider, "model": c.model,
                "base_url": c.base_url.rstrip("/")}

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {self.config.api_key}"}

    def remember(self, message: Message, raw) -> Message:
        message.provider_data = {"source": self.source, "fingerprint": fingerprint(message),
                                 "raw": copy.deepcopy(raw)}
        return message

    def replay(self, message: Message):
        data = message.provider_data
        if data.get("source") == self.source and data.get("fingerprint") == fingerprint(message):
            return copy.deepcopy(data.get("raw"))
        # Canonical content is authoritative after editing or switching a route.
        # Opaque reasoning is never transferred to a different route.
        return None

    def encode(self, messages: list[Message], tools: list[ToolSpec]) -> dict:
        raise NotImplementedError

    def decode(self, response: dict) -> ModelReply:
        raise NotImplementedError

    def complete(self, messages: list[Message], tools: list[ToolSpec] | None = None) -> ModelReply:
        response = self.client.post(self.config.base_url.rstrip("/") + self.endpoint,
                                    headers=self.headers(), json=self.encode(messages, tools or []),
                                    timeout=self.config.timeout)
        response.raise_for_status()
        return self.decode(response.json())

    def _events(self, messages, tools):
        body = self.encode(messages, tools or [])
        body["stream"] = True
        if self.config.api == "openai-chat":
            body["stream_options"] = {"include_usage": True}
        with self.client.stream("POST", self.config.base_url.rstrip("/") + self.endpoint,
                                headers=self.headers(), json=body, timeout=self.config.timeout) as response:
            response.raise_for_status()
            for event in sse_data(response.iter_lines()):
                if isinstance(event, dict) and (event.get("type") == "error" or "error" in event):
                    raise ProtocolError("服务商在流中返回错误")
                yield event

    def stream(self, messages, tools=None) -> Iterator[StreamEvent]:
        raise NotImplementedError
