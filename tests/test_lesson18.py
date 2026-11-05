from contextlib import contextmanager

import httpx
import pytest

from lessons import ch18
from minicode.testing import FakeModel
from minicode.types import Message, ModelReply, Usage


def test_retry_before_send_only_retries_connect_errors():
    wrapper = ch18.RetryBeforeSend(FakeModel([httpx.ConnectError("first"), "ok"]), delay=0)
    assert ch18.message_text(wrapper.complete([]).message) == "ok"
    assert wrapper.retries == 1
    wrapper = ch18.RetryBeforeSend(FakeModel([httpx.ReadTimeout("unknown result"), "wrong"]), delay=0)
    with pytest.raises(httpx.ReadTimeout):
        wrapper.complete([])
    assert wrapper.retries == 0


def test_runtime_checkpoints(capsys):
    for step in ("minimal", "retry", "deadline", "context", "processes", "enhanced"):
        assert ch18.main(["--offline", "--step", step]) == 0, step
    assert "second" in capsys.readouterr().out


def test_read_claim_does_not_pass_checkpoint(tmp_path):
    final = ch18.recorded_agent(FakeModel(["answer = 42"]), tmp_path)
    assert final["status"] == "success"
    assert not final["checkpoint_passed"]


@pytest.mark.parametrize("step", ["minimal", "retry", "context"])
def test_truncated_reply_fails_each_text_checkpoint(step, monkeypatch):
    truncated = ModelReply(Message.text("assistant", "部分回答"), Usage(), "length")
    @contextmanager
    def partial_model(*_):
        script = ["摘要正常", truncated] if step == "context" else [truncated]
        yield FakeModel(script)
    monkeypatch.setattr(ch18, "open_model", partial_model)
    assert ch18.main(["--offline", "--step", step]) == 1


def test_timeout_checkpoint_observes_actual_wait(tmp_path):
    from minicode.testing import call
    assert ch18.deadline_demo(FakeModel([call("Wait"), "完成"]), tmp_path, 0.01, expect_timeout=True)
    assert not ch18.deadline_demo(FakeModel(["完成"]), tmp_path, 0.01, expect_timeout=True)


def test_long_task_compacts_without_waiting_for_another_user_message():
    history = ch18.long_task_history()
    users = [message for message in history if message.role == "user"]
    assert len(users) == 1
    model = FakeModel(["目标是找出 answer，已经核对了早期记录。", "还没有找到具体数值。"])
    assert ch18.compact_once(model)
    request = model.requests[-1]["messages"]
    assert any(message.role == "user" and ch18.message_text(message) == "找出 answer 的值。"
               for message in request)
    calls = {part.id for message in request for part in message.content if part.type == "tool_call"}
    results = {part.call_id for message in request for part in message.content if part.type == "tool_result"}
    assert calls == results


def test_second_log_page_failure_cannot_pass_process_checkpoint(monkeypatch):
    from minicode.types import ToolResult
    monkeypatch.setattr(ch18.ProcessManager, "read_log", lambda *args, **kwargs: ToolResult("", "read failed", True))
    assert not ch18.process_pages()
