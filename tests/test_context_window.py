import pytest

from minicode.compact import CompactingModel, ContextWindowExceeded, estimate_tokens
from minicode.testing import FakeModel, call
from minicode.types import Message, ModelReply, StreamEvent, ToolResult, Usage, message_text, tool_calls


def long_task(rounds=5, width=300):
    messages = [Message.text("system", "keep system"), Message.text("user", "implement exact user goal")]
    for index in range(rounds):
        request = call("Read", path=f"{index}.py")
        messages.extend([Message("assistant", [request]),
                         Message("tool", [ToolResult(request.id, str(index) + "x" * width)])])
    return messages


def assert_complete_tool_pairs(messages):
    pending = set()
    for message in messages:
        if message.role == "assistant":
            assert not pending
            pending.update(request.id for request in tool_calls(message))
        elif message.role == "tool":
            pending.difference_update(part.call_id for part in message.content)
        else:
            assert not pending
    assert not pending


def test_single_prompt_tool_loop_compacts_and_keeps_goal_and_recent_pairs():
    original = long_task()
    model = FakeModel(["older results summary", "answer"])
    wrapper = CompactingModel(model, threshold_chars=100, keep_turns=2)
    wrapper.complete(original)
    sent = model.requests[-1]["messages"]
    assert len(sent) < len(original)
    assert message_text(sent[0]) == "keep system"
    assert any(message_text(message) == "implement exact user goal" for message in sent)
    assert sent[-4:] == original[-4:]
    assert_complete_tool_pairs(sent)
    assert len(original) == 12


def test_token_window_triggers_compaction_before_char_threshold():
    original = long_task(width=500)
    observed = []
    model = FakeModel(["short summary", "combined summary", "answer"])
    wrapper = CompactingModel(model, threshold_chars=1_000_000, keep_turns=1,
                              context_window=1000, reserve_output_tokens=100, observer=observed.append)
    wrapper.complete(original)
    sent = model.requests[-1]["messages"]
    assert estimate_tokens(sent) + 100 <= 1000
    assert any(event["type"] == "compaction_end" and event["success"] for event in observed)
    assert all(event.get("estimator", "utf8_bytes_div_3") == "utf8_bytes_div_3" for event in observed)


def test_uncompressible_goal_fails_before_sending_oversized_request():
    model = FakeModel(["must not call"])
    wrapper = CompactingModel(model, context_window=500, reserve_output_tokens=100)
    with pytest.raises(ContextWindowExceeded, match="没有可压缩"):
        wrapper.complete([Message.text("user", "x" * 3000)])
    assert not model.requests


def test_context_overflow_recovers_once_with_complete_tool_pairs():
    model = FakeModel([ValueError("context_length_exceeded"), "summary", "answer"])
    wrapper = CompactingModel(model, threshold_chars=100_000)
    reply = wrapper.complete(long_task())
    assert message_text(reply.message) == "answer"
    assert len(model.requests) == 3
    assert_complete_tool_pairs(model.requests[-1]["messages"])


def test_repeated_overflow_is_not_retried_forever():
    model = FakeModel([ValueError("context_length_exceeded"), "summary", ValueError("context_length_exceeded")])
    wrapper = CompactingModel(model, threshold_chars=100_000)
    with pytest.raises(ValueError, match="context_length_exceeded"):
        wrapper.complete(long_task())
    assert len(model.requests) == 3


def test_stream_does_not_restart_after_visible_output():
    class Partial:
        def stream(self, messages, tools=()):
            yield StreamEvent("text_delta", text="already visible")
            raise ValueError("context_length_exceeded")

        def complete(self, messages, tools=()):
            raise AssertionError("must not try to summarize after visible output")

    events = CompactingModel(Partial(), threshold_chars=100_000).stream(long_task())
    assert next(events).text == "already visible"
    with pytest.raises(ValueError, match="context_length_exceeded"):
        next(events)


def test_summary_failure_reports_failure_and_keeps_usage_on_legacy_fallback():
    failure = ModelReply(Message.text("assistant", "partial"), Usage(7, 3), "length")
    model = FakeModel([failure, ModelReply(Message.text("assistant", "answer"), Usage(2, 1), "stop")])
    observed = []
    wrapper = CompactingModel(model, threshold_chars=1, observer=observed.append)
    original = long_task()
    reply = wrapper.complete(original)
    assert reply.usage == Usage(9, 4)
    assert wrapper.last_error
    assert model.requests[-1]["messages"] == original
    assert any(event["type"] == "compaction_end" and not event["success"] for event in observed)


def test_summary_uses_separate_provider_entry_and_counts_budget_once():
    from minicode.budget import Budget, BudgetModel

    class Model(FakeModel):
        summary_calls = 0

        def summary_complete(self, messages):
            self.summary_calls += 1
            return ModelReply(Message.text("assistant", "summary"), Usage(10, 4), "stop")

    backend = Model([ModelReply(Message.text("assistant", "answer"), Usage(20, 3), "stop")])
    budget = Budget(100)
    wrapper = CompactingModel(BudgetModel(backend, budget), threshold_chars=1)
    reply = wrapper.complete(long_task())
    assert backend.summary_calls == 1
    assert budget.usage == Usage(30, 7)
    assert reply.usage == Usage(30, 7)


def test_oversized_summary_is_rejected_without_retry_loop():
    original = long_task(rounds=4, width=500)
    backend = FakeModel(["x" * 10000])
    wrapper = CompactingModel(backend, threshold_chars=1, keep_turns=1,
                              context_window=1500, reserve_output_tokens=100)
    with pytest.raises(ContextWindowExceeded):
        wrapper.complete(original)
    assert len(backend.requests) == 1


@pytest.mark.parametrize("setting", [
    {"context_window": float("nan")}, {"context_window": float("inf")},
    {"context_window": 5000.5}, {"reserve_output_tokens": -1},
    {"reserve_output_tokens": 0.5}, {"threshold_chars": True}, {"keep_turns": 1.5},
])
def test_context_window_settings_are_validated(setting):
    with pytest.raises(ValueError):
        CompactingModel(FakeModel([]), **setting)



def test_compaction_error_trace_excludes_exception_payload():
    import json
    observed = []
    backend = FakeModel([RuntimeError("https://user:secret@example.invalid/?api_key=private-prompt"), "answer"])
    wrapper = CompactingModel(backend, threshold_chars=1, observer=observed.append)
    wrapper.complete(long_task())
    serialized = json.dumps(observed)
    assert "secret" not in serialized and "private-prompt" not in serialized and "example.invalid" not in serialized
    assert any(event.get("error_type") == "RuntimeError" for event in observed)
