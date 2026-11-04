"""Contract tests cross the HTTP boundary without credentials or network access."""
import copy
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from minicode.agent import Agent
from minicode.providers import ModelConfig, ProtocolError, create_model
from minicode.providers.base import sse_data
from minicode.providers.demo import frames, payload, run_demo
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, Text, ToolCall, ToolResult, ToolSpec, message_text, tool_calls

APIS = ["openai-chat", "openai-responses", "anthropic-messages"]
SPEC = ToolSpec("add", "add integers", {"type": "object", "properties": {
    "a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]})


def model(api, handler=None):
    client = httpx.Client(transport=httpx.MockTransport(handler or (lambda req: httpx.Response(200))))
    return create_model(ModelConfig(api, "test-model", "https://fixture.invalid/v1", "test-key"), client)


def history():
    return [Message.text("system", "Use tools"), Message.text("user", "add"),
            Message("assistant", [Text("checking"), ToolCall("call_a", "add", {"a": 2, "b": 3}),
                                  ToolCall("call_b", "add", {"a": 4, "b": 5})]),
            Message("tool", [ToolResult("call_a", "5")]),
            Message("tool", [ToolResult("call_b", "denied", True)])]


@pytest.mark.parametrize("api", APIS)
def test_tool_round_trip_with_mock_http(api):
    events, requests = run_demo(api)
    assert events[-1]["text"] == "结果是 5。"
    assert events[-1]["usage"] == {"input_tokens": 20, "output_tokens": 10}
    assert len([e for e in events if e["type"] == "tool_result"]) == 1
    assert "tools" in requests[0]
    if api == "openai-chat":
        assert requests[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "5"}
    elif api == "openai-responses":
        assert requests[1]["input"][-1] == {"type": "function_call_output", "call_id": "call_1", "output": "5"}
    else:
        assert requests[1]["messages"][-1]["content"] == [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "5", "is_error": False}]


@pytest.mark.parametrize("api", APIS)
def test_wire_contract_multiple_calls_error_and_schema(api):
    encoded = model(api).encode(history(), [SPEC])
    assert encoded["model"] == "test-model"
    if api == "openai-chat":
        assert encoded["tools"][0]["function"]["parameters"] == SPEC.parameters
        messages = encoded["messages"]
        assert messages[-2] == {"role": "tool", "tool_call_id": "call_a", "content": "5"}
        assert messages[-1]["content"] == "[tool error] denied"
        assert json.loads(messages[2]["tool_calls"][1]["function"]["arguments"]) == {"a": 4, "b": 5}
    elif api == "openai-responses":
        assert encoded["tools"][0]["strict"] is False
        assert "function" not in encoded["tools"][0]
        assert encoded["input"][-2] == {"type": "function_call_output", "call_id": "call_a", "output": "5"}
        assert encoded["input"][-1]["call_id"] == "call_b"
        assert encoded["store"] is False and encoded["include"] == ["reasoning.encrypted_content"]
    else:
        assert encoded["system"] == "Use tools"
        assert encoded["tools"][0]["input_schema"] == SPEC.parameters
        assert encoded["messages"][-1] == {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_a", "content": "5", "is_error": False},
            {"type": "tool_result", "tool_use_id": "call_b", "content": "denied", "is_error": True}]}


@pytest.mark.parametrize("api", APIS)
def test_decode_common_contract(api):
    result = model(api).decode(payload(api, True))
    assert result.stop_reason == "tool_calls"
    assert tool_calls(result.message)[0].arguments == {"a": 2, "b": 3}
    assert tool_calls(result.message)[0].id == "call_1"
    assert result.usage.input_tokens == 10
    assert message_text(result.message) == ""


@pytest.mark.parametrize("api", APIS)
def test_invalid_tool_arguments_are_preserved_for_runner(api):
    data = payload(api, True)
    if api == "openai-chat":
        data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{"
    elif api == "openai-responses":
        data["output"][0]["arguments"] = "{"
    else:
        data["content"][0]["input"] = []
    call = tool_calls(model(api).decode(data).message)[0]
    assert call.arguments is None and call.parse_error
    invoked = []
    runner = ToolRunner([Tool(SPEC, lambda args, ctx: invoked.append(args))], ToolContext(Path.cwd()))
    result = runner.run(call)
    assert result.is_error and not invoked


@pytest.mark.parametrize("api", APIS)
def test_length_is_not_success_and_cannot_execute_tool(api):
    data = payload(api, True)
    if api == "openai-chat":
        data["choices"][0]["finish_reason"] = "length"
    elif api == "openai-responses":
        data.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    else:
        data["stop_reason"] = "max_tokens"
    invoked = []
    runner = ToolRunner([Tool(SPEC, lambda args, ctx: invoked.append(args))], ToolContext(Path.cwd()))
    events = list(Agent(model(api, lambda req: httpx.Response(200, json=data)), runner).run("add"))
    assert events[-1]["status"] == "length"
    assert not invoked


@pytest.mark.parametrize("api", APIS)
def test_refusal_normalized(api):
    data = payload(api, False)
    if api == "openai-chat":
        data["choices"][0]["message"]["refusal"] = "no"
    elif api == "openai-responses":
        data["output"][0]["content"] = [{"type": "refusal", "refusal": "no"}]
    else:
        data["stop_reason"] = "refusal"
    assert model(api).decode(data).stop_reason == "refusal"


@pytest.mark.parametrize("api", APIS)
def test_protocol_metadata_replay_is_source_and_content_scoped(api):
    adapter = model(api)
    data = payload(api, True)
    if api == "openai-chat":
        data["choices"][0]["message"]["reasoning_content"] = "opaque"
    elif api == "openai-responses":
        data["output"].insert(0, {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"})
    else:
        data["content"].insert(0, {"type": "thinking", "thinking": "hidden", "signature": "opaque"})
    reply = adapter.decode(data)
    assert "opaque" in json.dumps(adapter.encode([reply.message], []))
    assert "test-key" not in json.dumps(reply.message.provider_data)
    other = create_model(replace(adapter.config, model="different"), adapter.client)
    assert "opaque" not in json.dumps(other.encode([reply.message], []))
    edited = copy.deepcopy(reply.message)
    tool_calls(edited)[0].arguments["a"] = 99
    encoded = adapter.encode([edited], [])
    assert "opaque" not in json.dumps(encoded)
    assert "99" in json.dumps(encoded)


def test_responses_call_id_and_item_id_remain_distinct():
    adapter = model("openai-responses")
    reply = adapter.decode(payload("openai-responses", True))
    assert tool_calls(reply.message)[0].id == "call_1"
    encoded = adapter.encode([reply.message, Message("tool", [ToolResult("call_1", "5")])], [])
    assert encoded["input"][0]["id"] == "fc_1"
    assert encoded["input"][1]["call_id"] == "call_1"


@pytest.mark.parametrize("api", APIS)
def test_http_auth_url_and_status_error(api):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(401, json={"error": "invalid key"})
    adapter = model(api, handle)
    with pytest.raises(httpx.HTTPStatusError):
        adapter.complete([Message.text("user", "hi")])
    request = seen[0]
    expected = {"openai-chat": "/v1/chat/completions", "openai-responses": "/v1/responses", "anthropic-messages": "/v1/messages"}[api]
    assert request.url.path == expected
    if api == "anthropic-messages":
        assert request.headers["x-api-key"] == "test-key"
        assert request.headers["anthropic-version"] == "2023-06-01"
    else:
        assert request.headers["authorization"] == "Bearer test-key"


def stream_model(api, records):
    body = "".join("data: " + (r if isinstance(r, str) else json.dumps(r)) + "\n\n" for r in records)
    return model(api, lambda request: httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}))


@pytest.mark.parametrize("api", APIS)
def test_streaming_same_tool_round_trip(api):
    events, _ = run_demo(api, stream=True)
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "结果是 5。"
    assert events[-1]["status"] == "success"


@pytest.mark.parametrize("api", APIS)
def test_disconnect_never_produces_complete_reply(api):
    records = list(frames(api, True))[:-1]
    adapter = stream_model(api, records)
    with pytest.raises(ProtocolError):
        list(adapter.stream([Message.text("user", "add")], [SPEC]))


@pytest.mark.parametrize("api", APIS)
def test_stream_error_event_raises(api):
    adapter = stream_model(api, [{"type": "error", "error": {"message": "unavailable"}}])
    with pytest.raises(ProtocolError):
        list(adapter.stream([Message.text("user", "hi")]))


def test_chat_interleaved_tool_argument_chunks():
    records = [
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "add", "arguments": '{"a":'}},
                                                 {"index": 1, "id": "b", "function": {"name": "add", "arguments": '{"a":4,'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 1, "function": {"arguments": '"b":5}'}},
                                                 {"index": 0, "function": {"arguments": '2,"b":3}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 3, "completion_tokens": 4}},
        "[DONE]",
    ]
    events = list(stream_model("openai-chat", records).stream([Message.text("user", "add")]))
    reply = events[-1].reply
    assert [(c.id, c.arguments) for c in tool_calls(reply.message)] == [("a", {"a": 2, "b": 3}), ("b", {"a": 4, "b": 5})]
    assert reply.usage.output_tokens == 4


def test_anthropic_stream_preserves_signature():
    records = [
        {"type": "message_start", "message": {"content": [], "usage": {"input_tokens": 2}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "reasoning"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}},
        {"type": "message_stop"},
    ]
    adapter = stream_model("anthropic-messages", records)
    reply = list(adapter.stream([Message.text("user", "hi")]))[-1].reply
    assert adapter.encode([reply.message], [])["messages"][0]["content"][0]["signature"] == "sig"
    assert reply.usage.input_tokens == 2 and reply.usage.output_tokens == 3


def test_sse_comments_multiline_and_partial_frame():
    assert list(sse_data([": ping", 'data: {"type":', 'data: "x"}', ""])) == [{"type": "x"}]
    with pytest.raises(ProtocolError):
        list(sse_data(['data: {"type":"x"}']))


def test_unknown_protocol_rejected():
    with pytest.raises(ValueError, match="未知 api"):
        create_model(ModelConfig("wrong", "m", "https://fixture.invalid", "key"))


def test_nonstandard_json_number_is_not_executable():
    data = payload("openai-chat", True)
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = '{"a":NaN,"b":3}'
    call = tool_calls(model("openai-chat").decode(data).message)[0]
    assert call.arguments is None and "NaN" in call.parse_error


@pytest.mark.parametrize("stop_reason, expected", [("max_tokens", "length"), ("tool_use", "error")])
def test_anthropic_partial_tool_json_keeps_terminal_reason_and_usage(stop_reason, expected):
    records = [
        {"type": "message_start", "message": {"role": "assistant", "content": [],
            "usage": {"input_tokens": 7, "output_tokens": 0}}},
        {"type": "content_block_start", "index": 0, "content_block": {
            "type": "tool_use", "id": "call_partial", "name": "add", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {
            "type": "input_json_delta", "partial_json": '{"a":'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": 11}},
        {"type": "message_stop"},
    ]
    invoked = []
    runner = ToolRunner([Tool(SPEC, lambda args, ctx: invoked.append(args))], ToolContext(Path.cwd()))
    agent = Agent(stream_model("anthropic-messages", records), runner, stream=True)
    result = list(agent.run("add"))[-1]
    assert result["status"] == expected
    assert result["usage"] == {"input_tokens": 7, "output_tokens": 11}
    assert not invoked
    assert not any(tool_calls(message) for message in agent.history)


def test_chat_stop_with_tool_calls_is_not_promoted_to_executable():
    data = payload("openai-chat", True)
    data["choices"][0]["finish_reason"] = "stop"
    invoked = []
    runner = ToolRunner([Tool(SPEC, lambda args, ctx: invoked.append(args))], ToolContext(Path.cwd()))
    adapter = model("openai-chat", lambda request: httpx.Response(200, json=data))
    assert adapter.decode(data).stop_reason == "stop"
    result = list(Agent(adapter, runner).run("add"))[-1]
    assert result["status"] == "error"
    assert not invoked
