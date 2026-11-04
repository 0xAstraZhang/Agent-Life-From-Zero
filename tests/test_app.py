import json
import pytest

from minicode.budget import Budget, BudgetModel
from minicode.cli import main
from minicode.evals import run_evals
from minicode.testing import FakeModel
from minicode.types import Message, ModelReply, Usage


def test_evals_check_real_files_and_expected_denials():
    assert all(result["passed"] for result in run_evals())


def test_demo_json_and_session_resume(tmp_path, capsys):
    session = tmp_path / "session.jsonl"
    args = ["--demo", "--json", "--cwd", str(tmp_path), "--session", str(session)]
    assert main(args) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["status"] == "success"
    assert any(e["type"] == "tool_result" for e in events)
    saved = session.read_text()
    assert main(args) == 2  # 不能悄悄覆盖已有会话。
    assert session.read_text() == saved
    capsys.readouterr()
    assert main(args + ["--resume"]) == 0
    capsys.readouterr()
    assert main(["--resume", "--session", str(tmp_path / "missing"), "--demo"]) == 2


def test_demo_stream_has_deltas_before_result(tmp_path, capsys):
    assert main(["--demo", "--stream", "--json", "--cwd", str(tmp_path)]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert any(e["type"] == "text_delta" for e in events)
    assert len([e for e in events if e["type"] == "result"]) == 1
    assert events[-1]["status"] == "success"


def test_budget_blocks_next_request_and_stream_counts_once():
    reply = ModelReply(Message.text("assistant", "done"), Usage(8, 2), "stop")
    fake = FakeModel([reply, "must not run"])
    budget = Budget(10)
    model = BudgetModel(fake, budget)
    events = list(model.stream([Message.text("user", "hello")]))
    assert events[-1].type == "done"
    assert budget.usage == Usage(8, 2)
    with pytest.raises(RuntimeError, match="预算"):
        model.complete([Message.text("user", "again")])
    assert len(fake.requests) == 1


def test_cli_resume_refreshes_explicit_context(tmp_path, monkeypatch, capsys):
    from minicode.session import SessionStore
    from minicode.types import message_text
    session = tmp_path / "history.jsonl"
    SessionStore(session).save([Message.text("system", "old instructions"), Message.text("user", "old task")])
    (tmp_path / "NEW.md").write_text("使用新说明", encoding="utf-8")
    skill = tmp_path / "skills" / "audit"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# audit\n检查测试覆盖\n", encoding="utf-8")
    model = FakeModel(["done"])
    model.source, model.close = {}, lambda: None
    monkeypatch.setattr("minicode.cli.create_model", lambda config: model)
    monkeypatch.setenv("MINICODE_API_KEY", "fake-test-key")
    assert main(["--model", "test", "--cwd", str(tmp_path), "--session", str(session),
                 "--resume", "--instructions", "NEW.md", "--skills", str(skill.parent), "-p", "continue"]) == 0
    request = model.requests[0]
    system = message_text(request["messages"][0])
    assert "使用新说明" in system and "audit" in system and "old instructions" not in system
    assert message_text(request["messages"][1]) == "old task"


@pytest.mark.parametrize("api", ["openai-chat", "openai-responses", "anthropic-messages"])
def test_cli_routes_and_subagent_isolation(api, tmp_path, monkeypatch, capsys):
    from minicode.testing import call
    from minicode.types import message_text
    (tmp_path / "data.txt").write_text("fact", encoding="utf-8")
    model = FakeModel([call("Delegate", task="read data"), call("Read", path="data.txt"),
                       "child summary", "parent answer"])
    model.source, model.close = {"api": api}, lambda: None
    configs = []
    def factory(config):
        configs.append(config)
        return model
    monkeypatch.setattr("minicode.cli.create_model", factory)
    monkeypatch.setenv("MINICODE_API_KEY", "fake-test-key")
    assert main(["--api", api, "--model", "test", "--cwd", str(tmp_path),
                 "--subagents", "--json", "-p", "parent task"]) == 0
    assert configs[0].api == api
    child_request = model.requests[1]
    assert {t.name for t in child_request["tools"]} == {"Read", "Glob", "Grep"}
    assert all("parent task" not in message_text(m) for m in child_request["messages"])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["status"] == "success" and "budget_usage" in events[-1]


def test_budget_stream_close_releases_inner_generator():
    from minicode.types import StreamEvent
    closed = []
    class Model:
        def stream(self, messages, tools=()):
            try:
                yield StreamEvent("text_delta", text="partial")
            finally:
                closed.append(True)
    stream = BudgetModel(Model(), Budget()).stream([])
    next(stream)
    stream.close()
    assert closed == [True]
