import json
import time
from dataclasses import replace

import httpx
import pytest

from minicode.providers import ModelConfig, ProtocolError, create_model
from minicode.providers.demo import frames, payload
from minicode.types import Message


PROMPT = [Message.text("user", "do not log this prompt")]


def model(handler, observer=None, **kwargs):
    c = ModelConfig("openai-chat", "test", "https://fixture.invalid/v1", "secret-key",
                    retry_base_delay=0, retry_max_delay=0, **kwargs)
    adapter = create_model(c, httpx.Client(transport=httpx.MockTransport(handler)))
    adapter.observer = observer
    return adapter


def test_retry_transient_failure_and_observe_every_attempt_without_secrets():
    calls, events = [], []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json=payload("openai-chat", False), headers={"x-request-id": "upstream-2"})
    reply = model(handler, events.append).complete(PROMPT)
    assert reply.stop_reason == "stop" and len(calls) == 2
    ends = [e for e in events if e["type"] == "request_end"]
    assert len(ends) == 2 and len({e["local_request_id"] for e in ends}) == 2
    assert ends[0]["usage"] is None and ends[0]["http_status"] == 429
    assert ends[1]["normalized_usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert ends[1]["request_id"] == "upstream-2"
    assert "secret-key" not in json.dumps(events) and "do not log" not in json.dumps(events)


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_permanent_errors_are_not_retried(status):
    calls = []
    adapter = model(lambda req: (calls.append(req) or httpx.Response(status)))
    with pytest.raises(httpx.HTTPStatusError):
        adapter.complete(PROMPT)
    assert len(calls) == 1


def test_retry_count_is_bounded_and_long_retry_after_is_not_shortened():
    calls = []
    adapter = model(lambda req: (calls.append(req) or httpx.Response(503)))
    with pytest.raises(httpx.HTTPStatusError):
        adapter.complete(PROMPT)
    assert len(calls) == 3
    calls.clear()
    adapter = model(lambda req: (calls.append(req) or httpx.Response(429, headers={"Retry-After": "120"})))
    with pytest.raises(httpx.HTTPStatusError):
        adapter.complete(PROMPT)
    assert len(calls) == 1


def test_deadline_stops_before_request_and_bounds_request_timeout():
    requests = []
    adapter = model(lambda req: (requests.append(req) or httpx.Response(200, json=payload("openai-chat", False))))
    adapter.set_deadline(time.monotonic() - 1)
    with pytest.raises(TimeoutError):
        adapter.complete(PROMPT)
    assert not requests
    adapter.set_deadline(time.monotonic() + .5)
    adapter.complete(PROMPT)
    assert 0 < requests[0].extensions["timeout"]["read"] <= .5


class BrokenStream(httpx.SyncByteStream):
    def __iter__(self):
        yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
        raise httpx.ReadError("connection lost")


def test_disconnect_after_streamed_text_is_never_retried():
    calls, events = [], []
    adapter = model(lambda req: (calls.append(req) or httpx.Response(200, stream=BrokenStream())), events.append)
    with pytest.raises(httpx.ReadError):
        list(adapter.stream(PROMPT))
    assert len(calls) == 1
    ends = [event for event in events if event["type"] == "request_end"]
    assert len(ends) == 1 and ends[0]["usage"] is None


def test_stream_retries_before_first_event_and_counts_final_usage_once():
    calls, events = [], []
    body = "".join("data: " + (event if isinstance(event, str) else json.dumps(event)) + "\n\n"
                   for event in frames("openai-chat", False))
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, text=body)
    adapter = model(handler, events.append)
    result = list(adapter.stream(PROMPT))
    assert result[-1].reply.usage.output_tokens == 5 and len(calls) == 2
    assert len([event for event in events if event["type"] == "request_end" and event["usage"] is not None]) == 1


def test_close_stream_emits_cancelled_event_and_closes_connection():
    events = []
    adapter = model(lambda req: httpx.Response(200, stream=BrokenStream()), events.append)
    stream = adapter.stream(PROMPT)
    next(stream)
    stream.close()
    assert [e for e in events if e["type"] == "request_end"][0]["status"] == "cancelled"


def test_missing_usage_is_unknown_and_endpoint_credentials_are_redacted():
    events = []
    data = payload("openai-chat", False)
    del data["usage"]
    adapter = model(lambda req: httpx.Response(200, json=data), events.append)
    adapter.config = replace(adapter.config, base_url="https://user:password@fixture.invalid/v1?token=private")
    adapter.complete(PROMPT)
    end = [e for e in events if e["type"] == "request_end"][0]
    assert end["normalized_usage"] is None
    text = json.dumps(events)
    assert "password" not in text and "private" not in text


def test_local_configuration_transport_error_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.UnsupportedProtocol("invalid scheme")
    with pytest.raises(httpx.UnsupportedProtocol):
        model(handler).complete(PROMPT)
    assert len(calls) == 1


def test_interrupt_records_cancelled_without_retry():
    events, calls = [], []
    def handler(request):
        calls.append(request)
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        model(handler, events.append).complete(PROMPT)
    assert len(calls) == 1
    assert [e for e in events if e["type"] == "request_end"][0]["status"] == "cancelled"


def test_known_usage_survives_protocol_parse_failure():
    events = []
    data = {"choices": [], "usage": {"prompt_tokens": 20, "completion_tokens": 4}}
    adapter = model(lambda req: httpx.Response(200, json=data), events.append)
    with pytest.raises(ProtocolError):
        adapter.complete(PROMPT)
    ends = [e for e in events if e["type"] == "request_end"]
    assert len(ends) == 1 and ends[0]["normalized_usage"] == {"input_tokens": 20, "output_tokens": 4}


def test_partial_usage_is_not_invented_as_zero():
    events = []
    data = payload("openai-chat", False)
    data["usage"] = {"completion_tokens": 4}
    model(lambda req: httpx.Response(200, json=data), events.append).complete(PROMPT)
    assert [e for e in events if e["type"] == "request_end"][0]["normalized_usage"] is None


def test_chat_stream_preserves_actual_response_model_and_identifier():
    body = "".join("data: " + (event if isinstance(event, str) else json.dumps({**event, "model": "actual-model", "id": "response-1"})) + "\n\n"
                   for event in frames("openai-chat", False))
    adapter = model(lambda req: httpx.Response(200, text=body))
    reply = list(adapter.stream(PROMPT))[-1].reply
    assert reply.metadata == {"model": "actual-model", "id": "response-1"}
