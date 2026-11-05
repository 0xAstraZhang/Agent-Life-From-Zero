"""第 09 章：项目说明实际进入模型请求。"""
from lessons.ch09 import project_prompt
from minicode.agent import Agent
from minicode.testing import FakeModel
from minicode.tool import ToolContext, ToolRunner
from minicode.types import message_text


def test_project_instruction_reaches_model(tmp_path):
    (tmp_path / "MINICODE.md").write_text("测试命令是 python -m pytest -x。", encoding="utf-8")
    model = FakeModel(["收到"])
    agent = Agent(model, ToolRunner([], ToolContext(tmp_path)), project_prompt(tmp_path))
    list(agent.run("怎样测试？"))
    assert "python -m pytest -x" in message_text(model.requests[0]["messages"][0])


def test_checkpoints(capsys):
    import runpy
    from pathlib import Path
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/09_context.py"))["main"]
    for step in ("minimal", "enhanced"):
        main(["--offline", "--step", step])
        assert "success" in capsys.readouterr().out


def test_model_failure_fails_checkpoint(monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    import runpy
    import pytest
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/09_context.py"))["main"]

    @contextmanager
    def broken_model(args, script):
        yield FakeModel([ConnectionError("请求失败")])

    monkeypatch.setitem(main.__globals__, "open_model", broken_model)
    with pytest.raises(RuntimeError, match="任务未完成"):
        main(["--offline"])
