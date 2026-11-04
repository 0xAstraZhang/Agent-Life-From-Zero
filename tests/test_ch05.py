from minicode.providers import ModelConfig, create_model
from minicode.providers.demo import run_demo
from minicode.types import Message, ToolCall, ToolResult


def test_anthropic_http_tool_roundtrip():
    events, requests = run_demo("anthropic-messages")
    assert events[-1]["status"] == "success"
    assert requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "call_1"


def test_results_are_grouped_without_losing_errors():
    model = create_model(ModelConfig("anthropic-messages", "fixture", "https://fixture.invalid", "fake"))
    try:
        messages = [Message("assistant", [ToolCall("a", "echo", {}), ToolCall("b", "echo", {})]),
                    Message("tool", [ToolResult("a", "ok")]), Message("tool", [ToolResult("b", "failed", True)])]
        wire = model.encode(messages, [])["messages"]
        assert len(wire) == 2 and wire[-1]["role"] == "user"
        assert [block["tool_use_id"] for block in wire[-1]["content"]] == ["a", "b"]
        assert wire[-1]["content"][1]["is_error"] is True
    finally:
        model.close()
