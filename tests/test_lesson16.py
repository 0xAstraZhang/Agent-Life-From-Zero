from contextlib import contextmanager

from lessons import ch16
from minicode.testing import FakeModel


def test_minimal_and_full_cli_use_the_requested_directory(tmp_path):
    (tmp_path / "lesson.py").write_text("answer = 42\n")
    for step in ("minimal", "enhanced"):
        assert ch16.main(["--offline", "--step", step, "--cwd", str(tmp_path)]) == 0


def test_default_cli_task_must_actually_call_glob(tmp_path, monkeypatch):
    @contextmanager
    def model_without_tool(*_):
        yield FakeModel(["已经列出了文件。"])
    monkeypatch.setattr(ch16, "open_model", model_without_tool)
    assert ch16.main(["--offline", "--cwd", str(tmp_path)]) == 1


def test_enhanced_cli_preserves_llm_config_without_key_in_arguments(monkeypatch, tmp_path):
    import minicode.cli
    for key in ("MODEL", "BASE_URL", "API_KEY"):
        monkeypatch.delenv(f"MINICODE_{key}", raising=False)
    monkeypatch.setenv("LLM_MODEL", "lesson-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://fixture.invalid/v1")
    monkeypatch.setenv("LLM_API_KEY", "test-key-never-an-argument")
    recorded = []
    monkeypatch.setattr(minicode.cli, "main", lambda options: recorded.extend(options) or 0)
    assert ch16.main(["--step", "enhanced", "--cwd", str(tmp_path)]) == 0
    assert recorded[recorded.index("--api-key-env") + 1] == "LLM_API_KEY"
    assert recorded[recorded.index("--model") + 1] == "lesson-model"
    assert "test-key-never-an-argument" not in recorded
