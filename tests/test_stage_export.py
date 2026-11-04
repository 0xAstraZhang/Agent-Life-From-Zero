from pathlib import Path
import json

import pytest

from scripts.stages import export_stage, run_isolated, stage_files


def test_export_rejects_nonempty_directory_without_modifying_it(tmp_path):
    target = tmp_path / "practice"
    target.mkdir()
    note = target / "my_work.py"
    note.write_text("do not overwrite", encoding="utf-8")
    with pytest.raises(ValueError, match="不会覆盖"):
        export_stage(3, target)
    assert note.read_text() == "do not overwrite"
    assert list(target.iterdir()) == [note]


def test_ch03_contains_only_learned_protocol_and_interfaces(tmp_path):
    stage = export_stage(3, tmp_path / "ch03")
    assert not (stage / "minicode/providers/responses.py").exists()
    assert not (stage / "minicode/providers/anthropic.py").exists()
    assert not (stage / "minicode/skills.py").exists()
    result = run_isolated(stage, "probe", '''
import inspect
from pathlib import Path
import minicode
from minicode import types
from minicode.agent import Agent
assert Path(minicode.__file__).resolve().is_relative_to(Path.cwd())
assert not hasattr(types, "StreamEvent")
assert not hasattr(types, "message_to_dict")
assert "stream" not in inspect.signature(Agent).parameters
assert "history" not in inspect.signature(Agent).parameters
try:
    import minicode.skills
except ModuleNotFoundError as error:
    assert "禁止回落" in str(error)
else:
    raise AssertionError("missing module leaked from final checkout")
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_missing_stage_module_cannot_fall_back_to_editable_checkout(tmp_path):
    stage = export_stage(3, tmp_path / "ch03")
    (stage / "minicode/providers/chat.py").unlink()
    result = run_isolated(stage, "probe", "from minicode.providers import create_model")
    assert result.returncode != 0
    assert "阶段中没有模块 minicode.providers.chat" in result.stderr


@pytest.mark.parametrize("chapter, has_history", [(8, False), (10, True)])
def test_streaming_precedes_session_interface(chapter, has_history, tmp_path):
    stage = export_stage(chapter, tmp_path / f"ch{chapter:02}")
    result = run_isolated(stage, "probe", f'''
import inspect
from minicode import types
from minicode.agent import Agent
assert hasattr(types, "StreamEvent")
assert hasattr(types, "message_to_dict") is {has_history!r}
assert ("history" in inspect.signature(Agent).parameters) is {has_history!r}
''')
    assert result.returncode == 0, result.stdout + result.stderr


def test_later_stages_preserve_prior_tests_and_shipping_metadata(tmp_path):
    files03, files13, files16 = stage_files(3), stage_files(13), stage_files(16)
    assert {p for p in files03 if p.startswith("tests/")} <= set(files13)
    assert "tests/test_app.py" not in files13 and "tests/test_app.py" in files16
    stage = export_stage(16, tmp_path / "ch16")
    project = (stage / "pyproject.toml").read_text()
    assert 'name = "agent-life-from-zero"' in project
    assert 'version = "0.2.0"' in project
    assert 'minicode = "minicode.cli:main"' in project
