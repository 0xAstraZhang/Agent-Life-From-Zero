"""本章测试只依赖本章及之前的模块。"""
from pathlib import Path

import pytest

from minicode.context import build_system_prompt, load_project_instructions


def test_project_instructions_are_explicit_and_bounded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "MINICODE.md").write_text("parent must not leak")
    assert load_project_instructions(root) == ""
    (root / "MINICODE.md").write_text("run pytest")
    assert "run pytest" in build_system_prompt(root, load_project_instructions(root))
    with pytest.raises(ValueError, match="工作区"):
        load_project_instructions(root, "../MINICODE.md")
    (root / "linked.md").symlink_to(tmp_path / "MINICODE.md")
    with pytest.raises(ValueError, match="工作区"):
        load_project_instructions(root, "linked.md")
    with pytest.raises(ValueError, match="超过"):
        load_project_instructions(root, max_chars=2)
