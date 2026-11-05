"""第 13 章：正文只在 Skill 调用之后进入历史。"""
from lessons.ch13 import LessonSkills, skill_tool
from minicode.agent import Agent
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner
from minicode.types import message_text


def test_skill_body_is_loaded_after_catalog(tmp_path):
    folder = tmp_path / "review"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# 评审\n检查代码。\n\n正文独有的检查步骤。", encoding="utf-8")
    library = LessonSkills(tmp_path)
    model = FakeModel([call("Skill", name="review"), "评审完成"])
    runner = ToolRunner([skill_tool(library)], ToolContext(tmp_path))
    list(Agent(model, runner, library.catalog()).run("评审修改"))
    assert "正文独有" not in message_text(model.requests[0]["messages"][0])
    assert "正文独有" in model.requests[1]["messages"][-1].content[0].content


def test_checkpoints(capsys):
    import runpy
    from pathlib import Path
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/13_skills.py"))["main"]
    for step in ("minimal", "enhanced"):
        main(["--offline", "--step", step])
        output = capsys.readouterr().out
        assert "success" in output and "用一组具体输入推演代码" in output
        if step == "enhanced":
            assert "写入均被拒绝" in output


def test_correct_answer_without_skill_load_fails_checkpoint(monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    import runpy
    import pytest
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/13_skills.py"))["main"]

    @contextmanager
    def skips_skill(args, script):
        yield FakeModel(["add 应该返回 a + b。"])

    monkeypatch.setitem(main.__globals__, "open_model", skips_skill)
    with pytest.raises(RuntimeError, match="没有通过 Skill"):
        main(["--offline"])
