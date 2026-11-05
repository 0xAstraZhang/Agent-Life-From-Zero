"""HTTP transport and replay rules shared by the three wire protocols."""
from __future__ import annotations

import copy
import json
import math
import time
import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
from dataclasses import asdict, dataclass, replace
from typing import Iterable, Iterator

import httpx

from jsonschema import Draft202012Validator, ValidationError

from ..types import Message, ModelReply, StreamEvent, ToolCall, ToolSpec, message_text, tool_calls
from .controls import request_controls, strict_tool_schema, restore_optional_arguments, validate_controls


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
    thinking: str | None = None
    reasoning_effort: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    tool_choice: str | None = None
    strict_tools: bool = False
    response_format: str | None = None
    response_schema: dict | None = None
    thinking_budget_tokens: int | None = None
    max_retries: int = 2
    retry_base_delay: float = 0.5
    retry_max_delay: float = 30

    def __post_init__(self):
        if not self.model or not self.base_url or not self.api_key:
            raise ValueError("model、base_url 和 api_key 都必须设置")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("timeout 必须是大于 0 的有限秒数")
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise ValueError("max_tokens 必须是大于 0 的整数")
        if self.chat_token_field not in {None, "max_tokens", "max_completion_tokens"}:
            raise ValueError("chat_token_field 只能是 max_tokens 或 max_completion_tokens")
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int) or not 0 <= self.max_retries <= 10:
            raise ValueError("max_retries 必须是 0–10 之间的整数")
        if (not all(math.isfinite(v) and v >= 0 for v in (self.retry_base_delay, self.retry_max_delay))
                or self.retry_base_delay > self.retry_max_delay):
            raise ValueError("重试延迟必须有限且满足 0 <= base <= max")
        validate_controls(self)

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

    def __init__(self, config: ModelConfig, client: httpx.Client | None = None, *, observer=None):
        self.config = config
        self.client = client or httpx.Client(timeout=config.timeout)
        self._owns_client = client is None
        self.observer = observer
        self.deadline = None
        self._local = threading.local()

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

    def tool_choice_for(self, messages):
        """强制选择（required / tool:名）只作用到本次用户输入后的第一次工具调用。

        之后每轮仍强制会让模型无法给出最终回答，只能耗尽轮数；此后不发送 tool_choice，交回服务商默认。
        """
        choice = self.config.tool_choice
        if choice == "required" or (choice or "").startswith("tool:"):
            for message in reversed(messages):
                if message.role == "user":
                    break
                if message.role == "assistant" and tool_calls(message):
                    return None
        return choice

    def controls(self, tools, messages=()):
        self._local.tool_schemas = {tool.name: tool.parameters for tool in tools}
        self._local.tool_choice = self.tool_choice_for(messages)
        return request_controls(self.config, tools, tool_choice=self._local.tool_choice)

    def tool_schema(self, schema):
        return strict_tool_schema(schema) if self.config.strict_tools else schema

    def parse_call(self, call_id, name, raw):
        call = parse_call(call_id, name, raw)
        if self.config.strict_tools and call.arguments is not None:
            schema = getattr(self._local, "tool_schemas", {}).get(name, {})
            call.arguments = restore_optional_arguments(call.arguments, schema)
        return call

    def _derived(self, **changes):
        # 共享连接、观察者和期限；只替换本次用途不该继承的控制参数。
        model = type(self)(replace(self.config, **changes), self.client, observer=self.observer)
        model.set_deadline(self.deadline)
        return model

    def summary_complete(self, messages):
        return self._derived(tool_choice=None, strict_tools=False,
                             response_format=None, response_schema=None).complete(messages, [])

    def delegate_model(self):
        """子 Agent 沿用路由与生成参数；工具选择和最终输出格式只属于父任务。"""
        return self._derived(tool_choice=None, response_format=None, response_schema=None)

    @property
    def effective_config(self):
        config = {key: value for key, value in asdict(self.config).items() if key != "api_key"}
        parts = urlsplit(config["base_url"])
        # URLs may contain credentials or signed query parameters.
        host = parts.hostname or ""
        if parts.port:
            host += f":{parts.port}"
        config["base_url"] = urlunsplit((parts.scheme, host, parts.path, "", ""))
        return config

    def set_deadline(self, deadline):
        self.deadline = deadline

    def _timeout(self):
        if self.deadline is None:
            return self.config.timeout
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("任务已超过截止时间")
        return min(self.config.timeout, remaining)

    def _observe(self, event):
        if self.observer is not None:
            self.observer(event)

    def _retry_delay(self, error, attempt):
        retryable = isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.WriteError,
                                       httpx.TimeoutException, httpx.RemoteProtocolError))
        response = error.response if isinstance(error, httpx.HTTPStatusError) else None
        if response is not None:
            retryable = response.status_code in (408, 409, 429, 500, 502, 503, 504, 529)
        if not retryable or attempt > self.config.max_retries:
            return None
        delay = min(self.config.retry_max_delay, self.config.retry_base_delay * 2 ** (attempt - 1))
        value = response.headers.get("retry-after") if response is not None else None
        if value:
            try:
                wait = float(value)
            except ValueError:
                try:
                    wait = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    wait = 0
            # A provider's requested wait longer than our policy is not shortened.
            if math.isfinite(wait) and wait > self.config.retry_max_delay:
                return None
            if math.isfinite(wait):
                delay = max(delay, max(0, wait))
        return delay

    def _wait_retry(self, delay):
        if self.deadline is not None and time.monotonic() + delay >= self.deadline:
            raise TimeoutError("重试等待将超过任务截止时间")
        time.sleep(delay)
        self._timeout()

    def finish(self, reply, response):
        reply.usage_details = copy.deepcopy(response.get("usage") or {})
        reply.metadata = {key: response[key] for key in ("id", "model") if key in response}
        # Validate requested tool policy before any call can reach an executor.
        calls = tool_calls(reply.message)
        # 按本次请求实际发送的选择验收；直接 decode 时退回配置值。
        choice = getattr(self._local, "tool_choice", self.config.tool_choice)
        if reply.stop_reason in ("stop", "tool_calls") and choice is not None:
            invalid = ((choice == "none" and bool(calls)) or
                       (choice == "required" and not calls) or
                       (choice.startswith("tool:") and (not calls or any(call.name != choice[5:] for call in calls))))
            if invalid:
                reply.stop_reason, reply.error = "error", "模型输出违反 tool_choice 策略"
        # Validation failures retain billed usage and cannot be reported as success.
        if self.config.response_format and reply.stop_reason == "stop":
            try:
                value = json.loads(message_text(reply.message), parse_constant=_reject_constant)
                if self.config.response_format == "json_object" and not isinstance(value, dict):
                    raise ValueError("最终回复必须是 JSON 对象")
                if self.config.response_schema is not None:
                    Draft202012Validator(self.config.response_schema).validate(value)
            except (ValueError, ValidationError) as error:
                reply.stop_reason = "error"
                reply.error = f"结构化输出验收失败：{type(error).__name__}"
        return reply

    def _start(self, operation, attempt, stream):
        self._local.request_id = None
        self._local.response_received = False
        self._local.usage = None
        self._local.initial_usage = {}
        event = {"type": "request_start", "operation_id": operation, "local_request_id": f"{operation}:{attempt}", "attempt": attempt,
                 "stream": stream, "timestamp": datetime.now(timezone.utc).isoformat(),
                 "config": self.effective_config}
        self._observe(event)
        return time.monotonic()

    def _normalized_usage(self, raw):
        if not isinstance(raw, dict):
            return None
        input_key = "prompt_tokens" if self.config.api == "openai-chat" else "input_tokens"
        output_key = "completion_tokens" if self.config.api == "openai-chat" else "output_tokens"
        if input_key not in raw or output_key not in raw:
            return None
        tokens = [raw[input_key], raw[output_key]]
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in tokens):
            return None
        if self.config.api == "anthropic-messages":
            cached = [raw.get(key, 0) for key in ("cache_read_input_tokens", "cache_creation_input_tokens")]
            if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in cached):
                return None
            tokens[0] += sum(cached)
        return {"input_tokens": tokens[0], "output_tokens": tokens[1]}

    def _end(self, operation, attempt, started, *, reply=None, error=None, status=None):
        raw = reply.usage_details if reply is not None else getattr(self._local, "usage", None)
        event = {"type": "request_end", "operation_id": operation, "local_request_id": f"{operation}:{attempt}", "attempt": attempt,
                 "timestamp": datetime.now(timezone.utc).isoformat(),
                 "elapsed_seconds": time.monotonic() - started,
                 "request_id": getattr(self._local, "request_id", None),
                 "status": status or ("success" if reply is not None and reply.stop_reason in ("stop", "tool_calls") else "error"),
                 "response_received": getattr(self._local, "response_received", False),
                 "stop_reason": reply.stop_reason if reply is not None else None,
                 "usage": copy.deepcopy(raw) if raw else None,
                 "normalized_usage": self._normalized_usage(raw)}
        if reply is not None:
            event["response_metadata"] = reply.metadata
        if error is not None:
            event["error_type"] = type(error).__name__
            if isinstance(error, httpx.HTTPStatusError):
                event["http_status"] = error.response.status_code
            # 未建立连接、或服务商以 4xx 拒绝（含 429）时没有生成，可确定不计费。
            # 读超时、5xx 和流中断可能已经计费，仍记为未知。
            event["unbilled"] = not raw and (
                isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout))
                or (isinstance(error, httpx.HTTPStatusError) and 400 <= error.response.status_code < 500))
        self._observe(event)

    def complete(self, messages: list[Message], tools: list[ToolSpec] | None = None) -> ModelReply:
        body = self.encode(messages, tools or [])
        operation = uuid4().hex
        for attempt in range(1, self.config.max_retries + 2):
            self._timeout()
            started = self._start(operation, attempt, False)
            try:
                response = self.client.post(self.config.base_url.rstrip("/") + self.endpoint,
                                            headers=self.headers(), json=body, timeout=self._timeout())
                self._local.response_received = True
                self._local.request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    self._local.usage = copy.deepcopy(payload.get("usage"))
                self._timeout()
                reply = self.decode(payload)
            except BaseException as error:
                self._end(operation, attempt, started, error=error,
                          status="cancelled" if isinstance(error, KeyboardInterrupt) else None)
                delay = self._retry_delay(error, attempt)
                if delay is None:
                    raise
                self._observe({"type": "request_retry", "operation_id": operation,
                               "attempt": attempt, "delay_seconds": delay, "error_type": type(error).__name__})
                self._wait_retry(delay)
            else:
                self._end(operation, attempt, started, reply=reply)
                return reply
        raise AssertionError("unreachable")

    def _events(self, messages, tools):
        body = self.encode(messages, tools or [])
        body["stream"] = True
        if self.config.api == "openai-chat":
            body["stream_options"] = {"include_usage": True}
        with self.client.stream("POST", self.config.base_url.rstrip("/") + self.endpoint,
                                headers=self.headers(), json=body, timeout=self._timeout()) as response:
            self._local.response_received = True
            self._local.request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
            if response.is_error:
                # 流式响应体默认不读取；上下文超限等错误详情只在响应体里，抛出前先读出。
                response.read()
            response.raise_for_status()
            for event in sse_data(response.iter_lines()):
                self._timeout()
                if isinstance(event, dict):
                    kind = event.get("type")
                    if self.config.api == "openai-chat" and event.get("usage"):
                        self._local.usage = copy.deepcopy(event["usage"])
                    elif kind in ("response.completed", "response.incomplete", "response.failed"):
                        self._local.usage = copy.deepcopy(event.get("response", {}).get("usage"))
                    elif kind == "message_start":
                        self._local.initial_usage = copy.deepcopy(event.get("message", {}).get("usage") or {})
                    elif kind == "message_delta" and event.get("usage"):
                        self._local.initial_usage.update(event["usage"])
                        self._local.usage = copy.deepcopy(self._local.initial_usage)
                    if kind == "error" or "error" in event:
                        raise ProtocolError("服务商在流中返回错误")
                yield event

    def stream(self, messages, tools=None) -> Iterator[StreamEvent]:
        operation = uuid4().hex
        for attempt in range(1, self.config.max_retries + 2):
            self._timeout()
            started = self._start(operation, attempt, True)
            emitted = False
            source = iter(self._stream(messages, tools))
            completed = False
            try:
                for event in source:
                    self._timeout()
                    if event.type == "done":
                        self._end(operation, attempt, started, reply=event.reply)
                        completed = True
                    emitted = True
                    yield event
                if not completed:
                    raise ProtocolError("流没有最终结果")
                return
            except GeneratorExit:
                if not completed:
                    self._end(operation, attempt, started, status="cancelled")
                raise
            except BaseException as error:
                if not completed:
                    self._end(operation, attempt, started, error=error,
                              status="cancelled" if isinstance(error, KeyboardInterrupt) else None)
                delay = None if emitted else self._retry_delay(error, attempt)
                if delay is None:
                    raise
                self._observe({"type": "request_retry", "operation_id": operation,
                               "attempt": attempt, "delay_seconds": delay, "error_type": type(error).__name__})
                # Close the old connection before waiting and starting a replacement request.
                source.close()
                self._wait_retry(delay)
            finally:
                source.close()

    def _stream(self, messages, tools=None) -> Iterator[StreamEvent]:
        raise NotImplementedError
