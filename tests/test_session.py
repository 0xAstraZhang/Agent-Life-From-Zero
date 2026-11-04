"""本章测试只依赖本章及之前的模块。"""
from pathlib import Path

import pytest

from minicode.agent import Agent
from minicode.session import SessionStore, run_session
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, ToolCall, ToolResult, ToolSpec


def make_runner(tmp_path, outputs=None):
    outputs = outputs if outputs is not None else []
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    read = Tool(ToolSpec("Read", "read", schema), lambda args, ctx: "file content", True, True)
    write = Tool(ToolSpec("Write", "write", schema), lambda args, ctx: outputs.append("write") or "ok")
    return ToolRunner([read, write], ToolContext(tmp_path))

def test_session_preserves_metadata_and_repairs_without_execution(tmp_path):
    first, second = call("Read"), call("Write")
    history = [Message.text("user", "check"), Message("assistant", [first, second], {"source": "demo"}),
               Message("tool", [ToolResult(first.id, "done")]), Message.text("user", "continue")]
    store = SessionStore(tmp_path / "s.jsonl", source={"api": "demo", "api_key": "secret"})
    store.save(history)
    restored = store.load()
    assert restored[1].provider_data == {"source": "demo"}
    assert restored[3].content[0].call_id == second.id
    assert restored[3].content[0].is_error
    assert restored[4].role == "user"
    assert len(history) == 4
    assert "secret" not in store.path.read_text()
    assert store.load(repair=False) == history

def test_session_save_after_generator_close_repairs(tmp_path):
    model = FakeModel([call("Write"), "done"])
    touched = []
    agent = Agent(model, make_runner(tmp_path, touched))
    store = SessionStore(tmp_path / "s.jsonl")
    events = run_session(agent, "write", store)
    assert next(events)["type"] == "assistant"
    events.close()
    history = store.load(repair=False)
    assert history[-1].content[0].is_error
    assert not touched

def test_session_rejects_corrupt_version(tmp_path):
    store = SessionStore(tmp_path / "s.jsonl")
    store.path.write_text('{"type":"session","version":999}\n')
    with pytest.raises(ValueError, match="版本"):
        store.load()

@pytest.mark.parametrize("history", [
    [Message("tool", [ToolResult("missing", "result")])],
    [Message("assistant", [ToolCall("a", "Read", {})]),
     Message("tool", [ToolResult("other", "result")])],
    [Message("assistant", [ToolCall("a", "Read", {})]),
     Message("tool", [ToolResult("a", "first"), ToolResult("a", "second")])],
])
def test_session_rejects_broken_pairing(tmp_path, history):
    store = SessionStore(tmp_path / "s.jsonl")
    store.save(history)
    with pytest.raises(ValueError):
        store.load()
