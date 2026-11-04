"""本章测试只依赖本章及之前的模块。"""
from pathlib import Path

import pytest

from minicode.skills import SkillLibrary
from minicode.testing import call
from minicode.tool import ToolContext, ToolRunner


def test_skills_disclose_on_demand_and_reject_escape(tmp_path):
    directory = tmp_path / "skills"
    skill = directory / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\nRead code carefully.\n\nDetailed body.")
    library = SkillLibrary(directory)
    assert "review: Read code carefully." in library.catalog()
    assert "Detailed body" not in library.catalog()
    assert "Detailed body" in library.load("review")
    with pytest.raises(ValueError):
        library.load("../outside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("private")
    (directory / "linked").symlink_to(outside)
    assert "linked" not in library.catalog()
    with pytest.raises(ValueError):
        library.load("linked")

def test_skill_oversize_returns_tool_error(tmp_path):
    folder = tmp_path / "big"
    folder.mkdir()
    (folder / "SKILL.md").write_text("x" * 100)
    library = SkillLibrary(tmp_path, max_chars=20)
    runner = ToolRunner([library.tool()], ToolContext(tmp_path))
    result = runner.run(call("Skill", name="big"))
    assert result.is_error


def test_adding_skill_keeps_permissions_hooks_and_existing_tools(tmp_path):
    from minicode.coding_tools import build_coding_tools
    from minicode.hooks import HookRunner
    from minicode.permissions import Approval, PermissionRunner

    folder = tmp_path / "skills" / "review"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("# Review\nCheck the changes.\n", encoding="utf-8")
    library = SkillLibrary(folder.parent)
    context = ToolContext(tmp_path)
    tools = build_coding_tools(include_bash=False)
    approval = Approval()
    records = []

    def before(request):
        records.append(("before", request.name))

    def after(request, result):
        records.append(("after", request.name, result.is_error))

    def assemble(current_tools):
        inner = ToolRunner(current_tools, context)
        permissions = PermissionRunner(inner, approval=approval, allowed=(), read_only=False)
        return HookRunner(permissions, before=before, after=after)

    original = assemble(tools)
    assert original.run(call("Write", path="blocked.txt", content="no")).is_error
    tools.append(library.tool())
    upgraded = assemble(tools)
    loaded = upgraded.run(call("Skill", name="review"))
    assert not loaded.is_error and "Check the changes." in loaded.content
    assert upgraded.run(call("Write", path="blocked.txt", content="no")).is_error
    assert not (tmp_path / "blocked.txt").exists()
    assert upgraded.inner.approval is original.inner.approval
    assert upgraded.tools["Read"] is original.tools["Read"]
    assert records == [
        ("before", "Write"), ("after", "Write", True),
        ("before", "Skill"), ("after", "Skill", False),
        ("before", "Write"), ("after", "Write", True),
    ]
