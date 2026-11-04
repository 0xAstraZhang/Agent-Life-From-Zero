"""本章测试只依赖本章及之前的模块。"""
from pathlib import Path

import pytest

from minicode.compact import CompactingModel
from minicode.testing import FakeModel, call
from minicode.types import Message, ModelReply, ToolResult, Usage, message_text


def transcript():
    request = call("Read")
    return [Message.text("system", "system"), Message.text("user", "old goal"),
            Message("assistant", [request]), Message("tool", [ToolResult(request.id, "x" * 300)]),
            Message.text("assistant", "old answer"), Message.text("user", "NEW goal, keep exact")]

def test_compaction_keeps_turns_and_full_history_and_counts_summary():
    history = transcript()
    summary = ModelReply(Message.text("assistant", "old summary"), Usage(8, 3), "stop")
    final = ModelReply(Message.text("assistant", "answer"), Usage(5, 2), "stop")
    backend = FakeModel([summary, final, "again"])
    model = CompactingModel(backend, threshold_chars=1, keep_turns=1)
    result = model.complete(history, [])
    assert result.usage == Usage(13, 5)
    sent = backend.requests[1]["messages"]
    assert message_text(sent[-1]) == "NEW goal, keep exact"
    assert all(m.role != "tool" for m in sent)
    assert "old summary" in message_text(sent[1])
    assert len(history[3].content[0].content) == 300
    model.complete(history, [])
    assert model.compactions == 1
    assert len(backend.requests) == 3  # 同一前缀的摘要重用，不再请求模型。

def test_compaction_failure_preserves_original_request():
    history = transcript()
    backend = FakeModel([ModelReply(Message.text("assistant", "partial"), Usage(), "length"), "ok"])
    model = CompactingModel(backend, threshold_chars=1, keep_turns=1)
    model.complete(history)
    assert backend.requests[1]["messages"] == history
    assert model.compactions == 0
    assert model.last_error

def test_compaction_stream_has_final_reply():
    backend = FakeModel(["summary", "result"])
    model = CompactingModel(backend, threshold_chars=1, keep_turns=1)
    events = list(model.stream(transcript()))
    assert events[-1].type == "done"
    assert message_text(events[-1].reply.message) == "result"

def test_compaction_stream_closes_underlying_iterator():
    closed = []

    class Model:
        def stream(self, messages, tools):
            from minicode.types import StreamEvent
            try:
                yield StreamEvent("text_delta", text="partial")
                yield StreamEvent("text_delta", text="never")
            finally:
                closed.append(True)

    events = CompactingModel(Model()).stream([Message.text("user", "question")])
    next(events)
    events.close()
    assert closed == [True]
