import copy
import importlib.util
from pathlib import Path

import pytest

from minicode.agent import Agent
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import (Message, ModelReply, StreamEvent, Text, ToolCall, ToolResult,
                            ToolSpec, Usage, message_from_dict, message_to_dict, tool_calls)


def runner(tmp_path, execute=None):
    spec = ToolSpec("echo", "返回输入", {
        "type": "object", "properties": {"value": {"type": "integer"}},
        "required": ["value"], "additionalProperties": False,
    })
    return ToolRunner([Tool(spec, execute or (lambda args, ctx: str(args["value"])),
                            read_only=True, parallel_safe=True)], ToolContext(tmp_path))


def test_tool_roundtrip_and_usage(tmp_path):
    first = ModelReply(Message("assistant", [Text("先查一下"), ToolCall("c1", "echo", {"value": 3})]),
                       Usage(10, 4), "tool_calls")
    final = ModelReply(Message.text("assistant", "结果是 3"), Usage(15, 5), "stop")
    model = FakeModel([first, final])
    agent = Agent(model, runner(tmp_path))
    events = list(agent.run("计算"))
    assert events[-1] == {"type": "result", "status": "success", "text": "结果是 3", "turns": 2,
                          "usage": {"input_tokens": 25, "output_tokens": 9}}
    assert model.requests[1]["messages"][-1].content == [ToolResult("c1", "3")]
    assert [message.role for message in agent.history] == ["system", "user", "assistant", "tool", "assistant"]


def test_bad_calls_get_results_without_crashing(tmp_path):
    calls = [ToolCall("bad_json", "echo", None, '{"value":', "unexpected end"),
             ToolCall("bad_schema", "echo", {"value": True}), ToolCall("missing", "unknown", {})]
    model = FakeModel([calls, "参数有问题"])
    agent = Agent(model, runner(tmp_path))
    events = list(agent.run("test"))
    results = [event["result"] for event in events if event["type"] == "tool_result"]
    assert [result.call_id for result in results] == ["bad_json", "bad_schema", "missing"]
    assert all(result.is_error for result in results)
    assert events[-1]["status"] == "success"


@pytest.mark.parametrize("reason", ["length", "refusal", "error", "cancelled"])
def test_nonfinal_stops_never_execute_calls(tmp_path, reason):
    touched = []
    reply = ModelReply(Message("assistant", [Text("未完成"), call("echo", value=2)]), Usage(), reason)
    agent = Agent(FakeModel([reply]), runner(tmp_path, lambda args, ctx: touched.append(args)))
    result = list(agent.run("test"))[-1]
    assert result["status"] == reason
    assert not touched
    assert not any(tool_calls(message) for message in agent.history)


def test_model_error_and_turn_limit(tmp_path):
    agent = Agent(FakeModel([ConnectionError("offline")]), runner(tmp_path))
    assert list(agent.run("test"))[-1]["status"] == "error"
    agent = Agent(FakeModel([call("echo", value=1)]), runner(tmp_path), max_turns=1)
    events = list(agent.run("test"))
    assert events[-1]["status"] == "max_turns"
    assert agent.history[-1].content[0].call_id == agent.history[-2].content[0].id


def test_callback_error_preserves_error_and_call_id(tmp_path):
    r = runner(tmp_path, lambda args, ctx: ToolResult("", "退出码 1", True))
    assert r.run(ToolCall("actual", "echo", {"value": 1})) == ToolResult("actual", "退出码 1", True)


def test_stream_waits_for_completed_reply(tmp_path):
    model = FakeModel([call("echo", value=4), "四"])
    agent = Agent(model, runner(tmp_path), stream=True)
    events = list(agent.run("test"))
    assert [e["text"] for e in events if e["type"] == "text_delta"] == ["四"]
    assert events[-1]["status"] == "success"


def test_stream_disconnect_does_not_execute_partial_tool(tmp_path):
    touched = []
    class BrokenStream:
        def stream(self, messages, tools):
            yield StreamEvent("tool_call_delta", call_id="partial", arguments_delta='{"value":1}')
            # 没有 done，即使 JSON 已经合法，也没有获得执行许可。
    agent = Agent(BrokenStream(), runner(tmp_path, lambda args, ctx: touched.append(args)), stream=True)
    assert list(agent.run("test"))[-1]["status"] == "error"
    assert not touched


def test_close_repairs_pending_calls_without_running_them(tmp_path):
    touched = []
    model = FakeModel([[call("echo", value=1), call("echo", value=2)], "继续"])
    agent = Agent(model, runner(tmp_path, lambda args, ctx: touched.append(args)))
    stream = agent.run("test")
    assert next(stream)["type"] == "assistant"
    stream.close()
    assert not touched
    assert all(message.content[0].is_error for message in agent.history[-2:])
    assert list(agent.run("请继续"))[-1]["status"] == "success"


def test_keyboard_interrupt_records_unknown_outcome(tmp_path):
    def interrupted(args, ctx):
        raise KeyboardInterrupt
    agent = Agent(FakeModel([call("echo", value=1)]), runner(tmp_path, interrupted))
    events = list(agent.run("test"))
    assert events[-1]["status"] == "cancelled"
    assert agent.history[-1].content[0].is_error
    assert "结果未知" in agent.history[-1].content[0].content


def test_duplicate_call_ids_are_not_executed(tmp_path):
    touched = []
    calls = [ToolCall("same", "echo", {"value": 1}), ToolCall("same", "echo", {"value": 2})]
    agent = Agent(FakeModel([calls]), runner(tmp_path, lambda args, ctx: touched.append(args)))
    assert list(agent.run("test"))[-1]["status"] == "error"
    assert not touched


def test_persistence_keeps_internal_content_and_opaque_metadata():
    message = Message("assistant", [Text("先检查"), ToolCall("c", "echo", {"value": 1})],
                      {"source": "fixture", "payload": [{"opaque": "keep me"}]})
    assert message_from_dict(message_to_dict(message)) == message
    with pytest.raises(ValueError, match="内容块不合法"):
        message_from_dict({"role": "user", "content": [{"type": "tool_call"}]})


def test_chapter01_snapshot_still_works(tmp_path):
    path = Path(__file__).resolve().parents[1] / "examples" / "01_agent_loop.py"
    spec = importlib.util.spec_from_file_location("chapter01", path)
    chapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(chapter)
    (tmp_path / "data.txt").write_text("17", encoding="utf-8")
    model = FakeModel([call("read_file", path="data.txt"), "17"])
    events = list(chapter.run(model, "读数", tmp_path))
    assert events[-1]["status"] == "success" and events[-1]["turns"] == 2
    assert model.requests[1]["messages"][-1].content[0].content == "17"
