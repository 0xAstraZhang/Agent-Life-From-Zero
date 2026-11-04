from minicode.providers import ModelConfig, create_model
from minicode.providers.demo import run_demo
from minicode.types import Message, ToolResult, tool_calls


def test_responses_http_tool_roundtrip():
    events, requests = run_demo("openai-responses")
    assert events[-1]["status"] == "success"
    assert requests[1]["input"][-1] == {"type": "function_call_output", "call_id": "call_1", "output": "5"}


def test_response_item_id_is_not_tool_call_id():
    model = create_model(ModelConfig("openai-responses", "fixture", "https://fixture.invalid", "fake"))
    try:
        reply = model.decode({"status": "completed", "output": [{"type": "function_call", "id": "item_1",
                             "call_id": "call_9", "name": "echo", "arguments": "{}"}]})
        assert tool_calls(reply.message)[0].id == "call_9"
        request = model.encode([reply.message, Message("tool", [ToolResult("call_9", "done")])], [])
        assert request["input"][-1]["call_id"] == "call_9"
        assert request["input"][0]["id"] == "item_1"
    finally:
        model.close()
