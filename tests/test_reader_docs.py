"""读者复制正文代码时，也必须能运行；不能只检查参考源码。"""
from pathlib import Path
import re
import subprocess
import sys
from textwrap import indent
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def blocks(chapter):
    return re.findall(r"```python\n(.*?)\n```", (ROOT / "docs" / chapter).read_text(), re.S)


def select(chapter, starts):
    code = blocks(chapter)
    chosen = []
    for start in starts:
        matches = [block for block in code if block.startswith(start)]
        assert len(matches) == 1, (chapter, start)
        chosen.append(matches[0])
    return "\n\n".join(chosen) + "\n"


def test_ch00_body_is_a_standalone_program(tmp_path):
    path = tmp_path / "hello.py"
    path.write_text(select("00-setup.md", ["from copy import deepcopy"]))
    result = subprocess.run([sys.executable, "-I", str(path)], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert "模型请求次数： 1" in result.stdout


def test_ch01_ch02_body_builds_without_final_minicode(tmp_path):
    package = tmp_path / "lessons"
    package.mkdir()
    (package / "__init__.py").write_text("")
    first = select("01-agent-loop.md", ["from copy import deepcopy", "_ids = count(1)",
                                         "def run(", "def main():"])
    second = select("02-tools.md", ["from collections.abc import Callable",
                                     "@dataclass\nclass ToolContext", "class Agent:", "def main():"])
    (package / "ch01.py").write_text(first)
    (package / "ch02.py").write_text(second)
    for name, turns in [("ch01", 2), ("ch02", 3)]:
        bootstrap = ("import sys,runpy; "
                     f"sys.path.insert(0, {str(tmp_path)!r}); "
                     f"runpy.run_module('lessons.{name}', run_name='__main__')")
        result = subprocess.run([sys.executable, "-I", "-c", bootstrap], cwd=tmp_path,
                                capture_output=True, text=True, check=True)
        assert "'status': 'success'" in result.stdout
        assert f"'turns': {turns}" in result.stdout


def test_ch03_patch_keeps_the_previous_method_parameter():
    candidates = [block for block in blocks("03-chat-completions.md")
                  if block.startswith("if request.parse_error")]
    assert len(candidates) == 1
    namespace = {"ToolResult": lambda **kwargs: kwargs, "call": lambda: None}
    exec("def run(self, request):\n" + indent(candidates[0], "    ") + "\n    return None\n", namespace)
    request = SimpleNamespace(id="c1", arguments=None, parse_error="bad JSON", raw_arguments="{")
    result = namespace["run"](None, request)
    assert result["call_id"] == "c1" and result["is_error"]
    request.arguments, request.parse_error = {}, ""
    assert namespace["run"](None, request) is None
