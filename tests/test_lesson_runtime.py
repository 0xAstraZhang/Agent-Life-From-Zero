"""学习入口默认使用真实客户端；离线选择必须显式，且资源会关闭。"""
from types import SimpleNamespace

import pytest

from lessons.runtime import open_model, parse_args, run_events


def test_default_learning_entry_uses_configured_model_and_closes_it(monkeypatch):
    from minicode import providers

    for field, value in {"MODEL": "lesson-model", "BASE_URL": "https://lesson.invalid/v1",
                         "API_KEY": "test-key", "PROVIDER": "lesson"}.items():
        monkeypatch.setenv(f"MINICODE_{field}", value)
    configs, closed = [], []
    model = SimpleNamespace(close=lambda: closed.append(True))

    def create(config):
        configs.append(config)
        return model

    monkeypatch.setattr(providers, "create_model", create)
    args = parse_args("lesson", [])
    assert not args.offline
    with pytest.raises(RuntimeError, match="task failed"):
        with open_model(args, ["this fixture must not be used"]) as selected:
            assert selected is model
            raise RuntimeError("task failed")
    assert closed == [True]
    assert configs[0].model == "lesson-model"
    assert configs[0].base_url == "https://lesson.invalid/v1"


def test_offline_run_does_not_open_network_and_records_actual_history(monkeypatch):
    import httpx
    from minicode.types import Message, message_text

    def forbidden(*args, **kwargs):
        raise AssertionError("offline checkpoint attempted a network request")

    monkeypatch.setattr(httpx.Client, "send", forbidden)
    args = parse_args("lesson", ["--offline"])
    with open_model(args, ["answer"]) as model:
        history = [Message.text("user", "question")]
        reply = model.complete(history)
        history.append(reply.message)
        assert message_text(reply.message) == "answer"
        assert len(model.requests[0]["messages"]) == 1


@pytest.mark.parametrize("events", [[], [{"type": "result", "status": "error", "text": "request failed"}],
                                    [{"type": "result", "status": "max_turns", "text": ""}]])
def test_learning_run_cannot_succeed_without_a_successful_final_event(events):
    with pytest.raises(RuntimeError):
        run_events(iter(events))
