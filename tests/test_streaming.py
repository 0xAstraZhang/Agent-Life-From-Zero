import pytest
from minicode.agent import Agent
from minicode.providers.demo import run_demo
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import StreamEvent, ToolSpec


@pytest.mark.parametrize("api", ["openai-chat", "openai-responses", "anthropic-messages"])
def test_streaming_roundtrip(api):
    events, requests = run_demo(api, stream=True)
    assert len(requests) == 2 and events[-1]["status"] == "success"
    assert "".join(e["text"] for e in events if e["type"] == "text_delta") == "结果是 5。"


def test_disconnected_stream_never_runs_partial_call(tmp_path):
    touched = []
    runner = ToolRunner([Tool(ToolSpec("echo", "echo", {"type": "object"}),
                             lambda args, ctx: touched.append(args))], ToolContext(tmp_path))
    class Disconnected:
        def stream(self, messages, tools):
            yield StreamEvent("tool_call_delta", call_id="a", arguments_delta="{}")
    assert list(Agent(Disconnected(), runner, stream=True).run("test"))[-1]["status"] == "error"
    assert not touched


def test_close_repairs_pending_calls(tmp_path):
    runner = ToolRunner([Tool(ToolSpec("echo", "echo", {"type": "object"}), lambda args, ctx: "done")], ToolContext(tmp_path))
    agent = Agent(FakeModel([call("echo")]), runner)
    events = agent.run("test")
    assert next(events)["type"] == "assistant"
    events.close()
    assert agent.history[-1].role == "tool"
    assert agent.history[-1].content[0].is_error
