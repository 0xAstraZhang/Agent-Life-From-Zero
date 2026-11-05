import copy

import httpx
import pytest

from minicode.providers import ModelConfig, create_model
from minicode.providers.demo import payload
from minicode.types import Message, ToolSpec


SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}},
          "required": ["answer"], "additionalProperties": False}
TOOL = ToolSpec("solve", "Solve", SCHEMA)


def config(api="openai-chat", provider="deepseek", model="deepseek-flash", **kwargs):
    return ModelConfig(api, model, "https://fixture.invalid/v1", "secret", provider=provider, **kwargs)


def encode(c, tools=()):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        return create_model(c, client).encode([Message.text("user", "Return JSON")], tools)


@pytest.mark.parametrize("api,expected", [
    ("openai-chat", {"thinking": {"type": "enabled"}, "reasoning_effort": "max"}),
    ("anthropic-messages", {"thinking": {"type": "enabled"}, "output_config": {"effort": "max"}}),
    ("openai-responses", {"reasoning": {"effort": "max"}}),
])
def test_deepseek_thinking_effort_wire_mapping(api, expected):
    body = encode(config(api, thinking="enabled", reasoning_effort="max"))
    for key, value in expected.items():
        assert body[key] == value


def test_deepseek_disable_thinking_and_sampling():
    body = encode(config(thinking="disabled", temperature=.2))
    assert body["thinking"] == {"type": "disabled"} and body["temperature"] == .2
    assert encode(config("openai-responses", thinking="disabled"))["reasoning"] == {"effort": "none"}


@pytest.mark.parametrize("kwargs", [
    {"temperature": .2}, {"top_p": .5}, {"reasoning_effort": "medium"},
    {"thinking": "disabled", "reasoning_effort": "high"}, {"strict_tools": True},
    {"tool_choice": "required"}, {"tool_choice": "tool:solve"},
    {"response_format": "json_schema", "response_schema": SCHEMA},
    {"temperature": float("nan")}, {"max_retries": 12}, {"retry_max_delay": float("inf")},
])
def test_deepseek_rejects_ignored_or_invalid_controls(kwargs):
    with pytest.raises(ValueError):
        config(**kwargs)


def test_unknown_provider_or_model_requires_explicit_supported_capability():
    with pytest.raises(ValueError, match="provider"):
        config(provider="custom", thinking="enabled")
    with pytest.raises(ValueError, match="模型 ID"):
        config(model="claude-opus", reasoning_effort="max")


@pytest.mark.parametrize("api,expected", [
    ("openai-chat", {"type": "function", "function": {"name": "solve"}}),
    ("openai-responses", {"type": "function", "name": "solve"}),
    ("anthropic-messages", {"type": "tool", "name": "solve"}),
])
def test_named_tool_selection(api, expected):
    body = encode(config(api, thinking="disabled", tool_choice="tool:solve"), [TOOL])
    assert body["tool_choice"] == expected
    with pytest.raises(ValueError, match="指定工具"):
        encode(config(api, thinking="disabled", tool_choice="tool:missing"), [TOOL])


@pytest.mark.parametrize("api,provider,model", [
    ("openai-chat", "openai", "gpt-5.4"),
    ("openai-responses", "openai", "gpt-5.4"),
    ("anthropic-messages", "anthropic", "claude-sonnet-4-6"),
])
def test_schema_mapping_and_local_validation(api, provider, model):
    c = config(api, provider, model, response_format="json_schema", response_schema=SCHEMA)
    body = encode(c, [TOOL])
    if api == "openai-chat":
        assert body["response_format"]["json_schema"]["schema"] == SCHEMA
    elif api == "openai-responses":
        assert body["text"]["format"]["schema"] == SCHEMA
    else:
        assert body["output_config"]["format"]["schema"] == SCHEMA
    data = payload(api, False)
    with httpx.Client() as client:
        adapter = create_model(c, client)
        # Normal termination with invalid structured content is an error, with usage retained.
        reply = adapter.decode(data)
        assert reply.stop_reason == "error" and reply.usage.output_tokens == 5
        assert reply.usage_details == data["usage"]
        # Tool turns do not need to be valid final JSON.
        assert adapter.decode(payload(api, True)).stop_reason == "tool_calls"


def test_json_object_rejects_blank_and_nonobject():
    c = config(response_format="json_object")
    with httpx.Client() as client:
        adapter = create_model(c, client)
        for text in ("  ", "[]", "NaN"):
            data = payload("openai-chat", False)
            data["choices"][0]["message"]["content"] = text
            assert adapter.decode(data).stop_reason == "error"
        data["choices"][0]["message"]["content"] = '{"answer": 42}'
        assert adapter.decode(data).stop_reason == "stop"


def test_usage_raw_breakdowns_are_preserved_without_double_counting():
    with httpx.Client() as client:
        adapter = create_model(config("anthropic-messages"), client)
        data = payload("anthropic-messages", False)
        data["usage"] = {"input_tokens": 10, "cache_read_input_tokens": 20,
                         "cache_creation_input_tokens": 5, "output_tokens": 12,
                         "output_tokens_details": {"reasoning_tokens": 8}}
        reply = adapter.decode(data)
        assert reply.usage.input_tokens == 35 and reply.usage.output_tokens == 12
        assert reply.usage_details == data["usage"]
        data["usage"]["input_tokens"] = 999
        assert reply.usage_details["input_tokens"] == 10


def test_native_anthropic_thinking_budget_and_strict_schema():
    c = config("anthropic-messages", "anthropic", "claude-sonnet-4-6", thinking="enabled",
               thinking_budget_tokens=2048, strict_tools=True)
    body = encode(c, [TOOL])
    assert body["thinking"]["budget_tokens"] == 2048
    assert body["tools"][0]["strict"] is True
    optional = copy.deepcopy(SCHEMA)
    optional.pop("required")
    body = encode(c, [ToolSpec("optional", "Optional", optional)])
    assert body["tools"][0]["input_schema"]["required"] == ["answer"]
    assert {"type": "null"} in body["tools"][0]["input_schema"]["properties"]["answer"]["anyOf"]


@pytest.mark.parametrize("api", ["openai-chat", "openai-responses", "anthropic-messages"])
def test_strict_optional_nulls_are_removed_before_execution(api):
    schema = {"type": "object", "properties": {
        "path": {"type": "string"}, "limit": {"type": "integer"},
        "nullable": {"type": ["null", "string"]},
        "nested": {"type": "object", "properties": {"optional": {"type": "integer"}}}},
        "required": ["path"], "additionalProperties": False}
    provider = "anthropic" if api == "anthropic-messages" else "openai"
    name = "claude-sonnet-4-6" if provider == "anthropic" else "gpt-5.4"
    c = config(api, provider, name, strict_tools=True)
    arguments = {"path": "file.py", "limit": None, "nullable": None, "nested": {"optional": None}}
    data = payload(api, True)
    import json
    if api == "openai-chat":
        call = data["choices"][0]["message"]["tool_calls"][0]
        call["function"].update(name="Read", arguments=json.dumps(arguments))
    elif api == "openai-responses":
        data["output"][0].update(name="Read", arguments=json.dumps(arguments))
    else:
        data["content"][0].update(name="Read", input=arguments)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=data))) as client:
        reply = create_model(c, client).complete([Message.text("user", "read")], [ToolSpec("Read", "Read", schema)])
    from minicode.types import tool_calls
    assert tool_calls(reply.message)[0].arguments == {"path": "file.py", "nullable": None, "nested": {}}
    assert "limit" in schema["properties"] and schema["required"] == ["path"]


@pytest.mark.parametrize("model,effort", [("o3", "none"), ("o3", "max"), ("gpt-5.4", "max"),
                                         ("gpt-6-missing", "high"), ("gpt-4.1", "low")])
def test_native_effort_compatibility_is_not_inferred_from_model_prefix(model, effort):
    with pytest.raises(ValueError):
        config(provider="openai", model=model, reasoning_effort=effort)


def test_summary_request_has_no_final_schema_or_forced_tool_and_is_observed():
    requests, events = [], []
    c = config(provider="openai", model="gpt-5.4", thinking="enabled", reasoning_effort="high",
               tool_choice="tool:Read", response_format="json_schema", response_schema=SCHEMA)
    import json
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payload("openai-chat", False))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        adapter = create_model(c, client)
        adapter.observer = events.append
        reply = adapter.summary_complete([Message.text("user", "summarize")])
    assert reply.stop_reason == "stop"
    assert "tool_choice" not in requests[0] and "response_format" not in requests[0]
    assert requests[0]["reasoning_effort"] == "high"
    assert c.tool_choice == "tool:Read" and c.response_format == "json_schema"
    assert len([e for e in events if e["type"] == "request_end"]) == 1


@pytest.mark.parametrize("parameter", ["temperature", "top_p"])
def test_zero_sampling_value_is_still_an_explicit_control(parameter):
    with pytest.raises(ValueError, match="provider"):
        config(provider="custom", **{parameter: 0})


def test_tool_choice_none_cannot_execute_a_noncompliant_provider_tool_call():
    with httpx.Client() as client:
        adapter = create_model(config(tool_choice="none"), client)
        reply = adapter.decode(payload("openai-chat", True))
        assert reply.stop_reason == "error" and "tool_choice" in reply.error
