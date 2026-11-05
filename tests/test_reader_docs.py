"""正文里的完整文件从空目录构建，不能借用参考实现。"""
from pathlib import Path

import pytest

from scripts.reader_project import export_reader, load_cases, reader_steps, verify_reader

ROOT = Path(__file__).resolve().parents[1]
POINTS = reader_steps(ROOT)
CASES = load_cases(ROOT)


def test_every_chapter_has_standalone_checkpoints_and_exact_cases():
    assert {point.chapter for point in POINTS} == set(range(19)), "00–18 章都应提供完整文件检查点"
    keys = {(point.chapter, point.name) for point in POINTS}
    assert keys == set(CASES), f"正文与验证夹具不一致：缺少 {keys - set(CASES)}，多余 {set(CASES) - keys}"


@pytest.mark.parametrize("point", POINTS, ids=lambda point: f"ch{point.chapter:02}-{point.name}")
def test_reader_builds_and_runs_the_documented_project(point, tmp_path):
    project = export_reader(point.chapter, tmp_path / "my-agent", point.name, ROOT)
    result = verify_reader(point.chapter, point.name, project, CASES[(point.chapter, point.name)], root=ROOT)
    assert result["returncode"] == CASES[(point.chapter, point.name)].get("expected_exit", 0)
