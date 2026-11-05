"""验证正文提取、累积、隔离、HTTP 注入与实际文件效果。"""
from pathlib import Path

import pytest

from scripts.reader_project import export_reader, reader_files, reader_steps, validate_dependencies, validate_sources, verify_reader


PROJECT = '[project]\nname = "reader-project"\nversion = "0.0.1"\nrequires-python = ">=3.12"\ndependencies = ["httpx"]\n'


def document(root, chapter, files, step="minimal"):
    folder = root / "docs"
    folder.mkdir(exist_ok=True)
    path = folder / f"{chapter:02}-lesson.md"
    text = "# A chapter\n\n"
    for name, content in files.items():
        language = "python" if name.endswith(".py") else "toml" if name.endswith(".toml") else "text"
        text += f"```{language} file={name} step={step}\n{content.rstrip()}\n```\n\n"
    path.write_text(text, encoding="utf-8")
    return path


def project(root, code):
    document(root, 0, {"pyproject.toml": PROJECT, "demo.py": code})
    return export_reader(0, root / "export", root=root)


def test_steps_replace_complete_files_and_preserve_earlier_files(tmp_path):
    path = document(tmp_path, 0, {"pyproject.toml": PROJECT, "model.py": "VALUE = 1", "demo.py": "print('old')"})
    path.write_text(path.read_text() + "```python file=demo.py step=enhanced\nprint('enhanced')\n```\n")
    document(tmp_path, 1, {"demo.py": "print('chapter one')", "notes/info.txt": "hello"})
    assert [(point.chapter, point.name) for point in reader_steps(tmp_path)] == [(0, "minimal"), (0, "enhanced"), (1, "minimal")]
    assert reader_files(0, "minimal", tmp_path)["demo.py"] == "print('old')\n"
    assert reader_files(0, root=tmp_path)["demo.py"] == "print('enhanced')\n"
    files = reader_files(1, root=tmp_path)
    assert files["model.py"] == "VALUE = 1\n" and files["demo.py"] == "print('chapter one')\n"
    exported = export_reader(1, tmp_path / "built", root=tmp_path)
    assert (exported / "notes/info.txt").read_text() == "hello\n"
    assert "python demo.py" in (exported / "README.md").read_text()


@pytest.mark.parametrize("name", ["../outside.py", "/tmp/outside.py", "nested/../../outside.py", "..\\outside.py", ".git/config"])
def test_file_paths_cannot_escape_project(tmp_path, name):
    document(tmp_path, 0, {name: "pass"})
    with pytest.raises(ValueError, match="相对路径"):
        reader_steps(tmp_path)


def test_export_keeps_existing_work(tmp_path):
    document(tmp_path, 0, {"pyproject.toml": PROJECT, "demo.py": "print('hello')"})
    destination = tmp_path / "mine"
    destination.mkdir()
    (destination / "my-notes.txt").write_text("keep")
    with pytest.raises(ValueError, match="不会覆盖"):
        export_reader(0, destination, root=tmp_path)
    assert list(destination.iterdir()) == [destination / "my-notes.txt"]
    assert (destination / "my-notes.txt").read_text() == "keep"


@pytest.mark.parametrize("source", ["from lessons.ch01 import run", "import minicode", "from examples.live_model import main", "__import__('minicode')"])
def test_static_repository_imports_are_rejected(source):
    with pytest.raises(ValueError, match="不能借用"):
        validate_sources({"demo.py": source})


def test_invalid_python_fails_before_export(tmp_path):
    document(tmp_path, 0, {"pyproject.toml": PROJECT, "demo.py": "if broken"})
    with pytest.raises(ValueError, match="无法编译"):
        export_reader(0, tmp_path / "invalid", root=tmp_path)
    assert not (tmp_path / "invalid").exists()


def test_undeclared_installed_dependency_is_rejected_before_export(tmp_path):
    document(tmp_path, 0, {"pyproject.toml": PROJECT, "demo.py": "import jsonschema\nprint('installed locally')"})
    with pytest.raises(ValueError, match="未声明依赖.*jsonschema"):
        export_reader(0, tmp_path / "invalid", root=tmp_path)
    assert not (tmp_path / "invalid").exists()


def test_dependency_check_uses_distribution_names_and_normalization():
    files = {"pyproject.toml": '[project]\ndependencies = ["Third.Package[extra]>=1"]',
             "demo.py": "import third_party.api"}
    validate_dependencies(files, {"third_party": ["third-package"]})
    files["pyproject.toml"] = '[project]\ndependencies = ["unrelated"]'
    with pytest.raises(ValueError, match="未声明依赖.*third-package"):
        validate_dependencies(files, {"third_party": ["third-package"]})


def test_dependency_check_accepts_stdlib_and_local_modules():
    files = {"pyproject.toml": "[project]\ndependencies = []",
             "demo.py": "import json\nfrom pathlib import Path\nfrom model import complete\nfrom helpers.io import load",
             "model.py": "def complete(): pass",
             "helpers/io.py": "from . import data\ndef load(): pass",
             "helpers/data.py": "DATA = 1"}
    validate_dependencies(files, {})


def test_validation_rechecks_dependencies_after_project_changes(tmp_path):
    exported = project(tmp_path, "import httpx\nprint('ready')")
    (exported / "pyproject.toml").write_text("[project]\ndependencies = []")
    with pytest.raises(ValueError, match="未声明依赖.*httpx"):
        verify_reader(0, "minimal", exported, {"responses": []}, root=tmp_path)


def test_validation_injects_all_clients_and_keeps_local_effects(tmp_path):
    exported = project(tmp_path, '''import httpx
from pathlib import Path
answer = input("allow? ")
with httpx.Client(headers={"Authorization": "Bearer reader-test-key"}) as first:
    a = first.post("https://reader.invalid/v1/chat/completions", json={"messages": [{"role": "user", "content": answer}]}).json()
with httpx.Client() as second:
    b = second.post("https://reader.invalid/v1/chat/completions", json={"previous": a["value"]}).json()
Path("result.txt").write_text(str(b["value"]))
print("actual", b["value"])
''')
    def check(requests, directory, output):
        assert requests[0]["json"]["messages"][0]["content"] == "y"
        assert requests[0]["headers"]["authorization"] == "<redacted>"
        assert requests[1]["json"] == {"previous": 5}
    result = verify_reader(0, "minimal", exported, {
        "responses": [{"value": 5}, {"value": 20}], "inputs": ["y"],
        "expected_output": ["actual 20"], "expected_files": {"result.txt": "20", "absent.txt": None}, "check": check,
    }, root=tmp_path)
    assert result["responses_used"] == 2


def test_sse_fixture_uses_real_stream_reader(tmp_path):
    exported = project(tmp_path, '''import httpx
with httpx.Client() as client:
    with client.stream("POST", "https://reader.invalid/stream", json={"stream": True}) as response:
        print("|".join(response.iter_lines()))
''')
    result = verify_reader(0, "minimal", exported, {"responses": [{"sse": "data: hello\n\ndata: done\n\n"}],
        "expected_output": ["data: hello||data: done"]}, root=tmp_path)
    assert result["requests"][0]["json"]["stream"] is True


def test_unused_or_extra_responses_fail_validation(tmp_path):
    exported = project(tmp_path, "print('no request')")
    with pytest.raises(AssertionError, match="HTTP 请求不符"):
        verify_reader(0, "minimal", exported, {"responses": [{"unused": True}]}, root=tmp_path)
    (exported / "demo.py").write_text('''import httpx
try:
    httpx.post("https://reader.invalid", json={})
except Exception:
    print("swallowed")
''')
    with pytest.raises(AssertionError, match="额外请求 1"):
        verify_reader(0, "minimal", exported, {"responses": []}, root=tmp_path)


def test_dynamic_repository_import_is_blocked_at_runtime(tmp_path):
    exported = project(tmp_path, "name = 'mini' + 'code'\n__import__(name)")
    with pytest.raises(RuntimeError, match="cannot import repository module minicode"):
        verify_reader(0, "minimal", exported, {"responses": []}, root=tmp_path)


def test_direct_socket_network_is_blocked(tmp_path):
    exported = project(tmp_path, "import socket\nsocket.create_connection(('example.com', 443))")
    with pytest.raises(RuntimeError, match="forbids real network"):
        verify_reader(0, "minimal", exported, {"responses": []}, root=tmp_path)


def test_child_python_network_is_blocked(tmp_path):
    exported = project(tmp_path, '''import subprocess
import sys
subprocess.run([sys.executable, "-c", "import socket; socket.create_connection(('example.com',443))"], check=True)
''')
    with pytest.raises(RuntimeError, match="forbids real network"):
        verify_reader(0, "minimal", exported, {"responses": []}, root=tmp_path)


def test_child_python_cannot_import_repository_packages(tmp_path):
    exported = project(tmp_path, '''import subprocess
import sys
subprocess.run([sys.executable, "-c", "name = 'mini' + 'code'; __import__(name)"], check=True)
''')
    with pytest.raises(RuntimeError, match="cannot import repository module minicode"):
        verify_reader(0, "minimal", exported, {"responses": []}, root=tmp_path)


def test_nonzero_exit_and_wrong_file_effect_are_rejected(tmp_path):
    exported = project(tmp_path, "raise RuntimeError('failed lesson')")
    with pytest.raises(RuntimeError, match="failed lesson"):
        verify_reader(0, "minimal", exported, {"responses": []}, root=tmp_path)
    (exported / "demo.py").write_text("print('done')")
    with pytest.raises(AssertionError, match="不符合预期"):
        verify_reader(0, "minimal", exported, {"responses": [], "expected_files": {"result.txt": "42"}}, root=tmp_path)
