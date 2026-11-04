import os
from pathlib import Path
import shlex
import sys

from minicode.coding_tools import MAX_OUTPUT, build_coding_tools
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall


def runner(tmp_path):
    return ToolRunner(build_coding_tools(), ToolContext(tmp_path))


def invoke(executor, name, **args):
    return executor.run(ToolCall("test-id", name, args))


def test_read_edit_and_content_freshness(tmp_path):
    file = tmp_path / "answer.py"
    file.write_text("answer = 41\n", encoding="utf-8")
    tools = runner(tmp_path)
    assert invoke(tools, "Edit", path="answer.py", old="41", new="42").is_error
    assert "1\tanswer = 41" in invoke(tools, "Read", path="answer.py").content
    assert not invoke(tools, "Edit", path="answer.py", old="41", new="42").is_error
    original_stat = file.stat()
    file.write_text("answer = 43\n", encoding="utf-8")
    os.utime(file, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    stale = invoke(tools, "Write", path="answer.py", content="answer = 44\n")
    assert stale.is_error and "已经改变" in stale.content
    assert file.read_text() == "answer = 43\n"


def test_root_and_symlink_boundary(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("outside")
    (root / "link").symlink_to(outside)
    tools = runner(root)
    for path in ("../secret.txt", str(outside), "link"):
        assert invoke(tools, "Read", path=path).is_error
        assert invoke(tools, "Write", path=path, content="changed").is_error
    (root / "out").symlink_to(tmp_path, target_is_directory=True)
    assert invoke(tools, "Write", path="out/new.txt", content="changed").is_error
    assert outside.read_text() == "outside"
    assert not (tmp_path / "new.txt").exists()


def test_unique_edit_and_scoped_search(tmp_path):
    tools = runner(tmp_path)
    assert not invoke(tools, "Write", path="src/a.py", content="one one\n").is_error
    assert invoke(tools, "Edit", path="src/a.py", old="one", new="two").is_error
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("one")
    assert "src/a.py" in invoke(tools, "Glob", pattern="*.py").content
    found = invoke(tools, "Grep", text="one").content
    assert "src/a.py:1:one one" in found and ".git" not in found


def test_bash_failure_timeout_and_output_bound(tmp_path):
    tools = runner(tmp_path)
    executable = shlex.quote(sys.executable)
    failed = invoke(tools, "Bash", command=f"{executable} -c 'raise SystemExit(7)'")
    assert failed.call_id == "test-id" and failed.is_error and "退出码 7" in failed.content
    timeout = invoke(tools, "Bash", command=f"{executable} -c 'import time; time.sleep(10)'", timeout=0.05)
    assert timeout.is_error and "超时" in timeout.content
    long = invoke(tools, "Bash", command=f"{executable} -c 'print(\"a\" * 100000)'")
    assert not long.is_error and len(long.content) < MAX_OUTPUT + 200


def test_schemas_distinguish_readonly_and_parallel(tmp_path):
    tools = runner(tmp_path).tools
    assert tools["Read"].read_only and tools["Read"].parallel_safe
    assert not tools["Bash"].read_only and not tools["Bash"].parallel_safe
    assert not invoke(runner(tmp_path), "Write", path="new.txt", content="ok").is_error
