"""第 08 章起：检查第 04–08 章最小原型的实际数据流。"""
import json
from pathlib import Path
import runpy
from contextlib import contextmanager

import httpx
import pytest

from lessons.coding_prototype import build_prototype_tools
from lessons.protocol_roundtrip import addition_runner
from lessons.streaming_prototype import stream_roundtrip
from lessons.anthropic_prototype import anthropic_roundtrip
from lessons.responses_prototype import responses_roundtrip
from minicode.agent import Agent
from minicode.providers import ModelConfig
from minicode.providers.demo import payload
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner
from minicode.types import Message, ModelReply, StreamEvent, ToolResult, Usage


@pytest.mark.parametrize("filename", ["04_responses.py", "05_anthropic.py"])
def test_protocol_chapter_rejects_a_different_api_before_sending(filename):
    example = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / filename))
    with pytest.raises(ValueError, match="其他协议"):
        example["main"](["--api", "openai-chat"])


@pytest.mark.parametrize("api,run", [
    ("openai-responses", responses_roundtrip), ("anthropic-messages", anthropic_roundtrip),
])
def test_direct_http_prototype_returns_real_tool_result_with_matching_id(api, run):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payload(api, len(requests) == 1))

    config = ModelConfig(api, "fixture-model", "https://fixture.invalid/v1", "fixture-key")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        assert run(client, config) == "结果是 5。"
    assert len(requests) == 2
    if api == "openai-responses":
        assert requests[1]["input"][-1] == {
            "type": "function_call_output", "call_id": "call_1", "output": "5"}
        assert requests[1]["input"][-2]["id"] == "fc_1"
    else:
        assert requests[1]["messages"][-1] == {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "5"}]}


def test_coding_prototype_reads_changes_and_checks_file(tmp_path):
    (tmp_path / "answer.txt").write_text("41\n", encoding="utf-8")
    model = FakeModel([call("Read"), call("Edit", old="41", new="42"), call("RunTests"), "完成"])
    runner = ToolRunner(build_prototype_tools(), ToolContext(tmp_path))
    events = list(Agent(model, runner).run("修改并检查"))
    results = [event for event in events if event["type"] == "tool_result"]
    assert (tmp_path / "answer.txt").read_text(encoding="utf-8") == "42\n"
    assert len(results) == 3 and not any(event["is_error"] for event in results)
    assert "检查通过" in model.requests[-1]["messages"][-1].content[0].content


@pytest.mark.parametrize("allowed", [True, False])
def test_permission_result_reaches_next_model_request(tmp_path, allowed):
    example = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/07_permissions.py"))
    runner = example["ConfirmRunner"](example["make_runner"](tmp_path), lambda name, args: allowed)
    model = FakeModel([call("WriteNote", content="hello"), "收到执行结果"])
    events = list(Agent(model, runner).run("创建文件"))
    assert events[-1]["status"] == "success"
    assert (tmp_path / "note.txt").exists() is allowed
    result = model.requests[1]["messages"][-1].content[0]
    original = model.requests[1]["messages"][-2].content[0]
    assert result.call_id == original.id
    assert result.is_error is not allowed


def test_streaming_prototype_displays_text_and_returns_tool_result():
    model = FakeModel([call("add", a=2, b=3), "结果是 5。"])
    fragments = []
    history = stream_roundtrip(model, addition_runner(), "计算", fragments.append)
    assert "".join(fragments) == "结果是 5。"
    assert model.requests[1]["messages"][-1].content[0].content == "5"
    assert [message.role for message in history] == ["user", "assistant", "tool", "assistant"]


@pytest.mark.parametrize("complete_but_truncated", [False, True])
def test_streaming_prototype_never_executes_incomplete_call(complete_but_truncated):
    executed = []

    class BrokenModel:
        def stream(self, messages, tools):
            yield StreamEvent("tool_call_delta", call_id="partial", arguments_delta='{"a":')
            if complete_but_truncated:
                reply = ModelReply(Message("assistant", [call("add", a=2, b=3)]), Usage(), "length")
                yield StreamEvent("done", reply=reply)

    class Runner:
        def specs(self):
            return addition_runner().specs()

        def run(self, request):
            executed.append(request)
            return ToolResult(request.id, "5")

    with pytest.raises(ValueError):
        stream_roundtrip(BrokenModel(), Runner(), "计算", lambda text: None)
    assert executed == []


@pytest.mark.parametrize("name,arguments", [
    ("04_responses.py", ["--step", "enhanced"]),
    ("05_anthropic.py", ["--step", "enhanced"]),
    ("06_coding_tools.py", []),
    ("06_coding_tools.py", ["--step", "enhanced"]),
    ("07_permissions.py", ["--decision", "allow"]),
    ("07_permissions.py", ["--step", "enhanced", "--decision", "deny"]),
    ("08_streaming.py", ["--step", "tools"]),
    ("08_streaming.py", ["--step", "enhanced"]),
])
@pytest.mark.parametrize("reply", ["我已完成任务", ConnectionError("模拟断网")])
def test_examples_reject_claimed_success_without_tools_and_request_failure(name, arguments, reply, monkeypatch):
    example = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples" / name))

    @contextmanager
    def open_fixture(args, script):
        yield FakeModel([reply])

    monkeypatch.setitem(example["main"].__globals__, "open_model", open_fixture)
    with pytest.raises((RuntimeError, ConnectionError)):
        example["main"](arguments)


@pytest.mark.parametrize("api,run", [
    ("openai-responses", responses_roundtrip), ("anthropic-messages", anthropic_roundtrip),
])
def test_direct_http_prototype_rejects_answer_without_executing_tool(api, run):
    config = ModelConfig(api, "fixture-model", "https://fixture.invalid/v1", "fixture-key")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload(api, False)))) as client:
        with pytest.raises(ValueError):
            run(client, config)
