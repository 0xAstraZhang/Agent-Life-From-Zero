import json
import os
from pathlib import Path
import shlex
import sys
import time
from uuid import uuid4

import pytest

from minicode.cli import main
from minicode.coding_tools import build_coding_tools
from minicode.sandbox import Sandbox, SandboxUnavailable, sandbox_tools
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall

posix_only = pytest.mark.skipif(os.name != "posix", reason="需要 /bin/sh")


def runner(root, sandbox):
    return ToolRunner(sandbox_tools(build_coding_tools(), sandbox), ToolContext(root))


def invoke(executor, name, **args):
    return executor.run(ToolCall("test-id", name, args))


def fake_executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def fake_bwrap(path, log, label="bwrap"):
    """假 bwrap：把 label 和参数追加到 log，然后执行 -- 之后的命令。"""
    return fake_executable(path, f'echo {label} >> {shlex.quote(str(log))}\n'
                           f'printf "%s\\n" "$@" >> {shlex.quote(str(log))}\n'
                           'while [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n')


def test_argv_orders_mounts_and_isolates_network(tmp_path):
    root, cache = tmp_path / "work", tmp_path / "cache"
    (root / ".git").mkdir(parents=True)
    cache.mkdir()
    argv = Sandbox(writable=(str(cache),)).argv("make test", root, environ={})
    assert argv[:4] == ["bwrap", "--ro-bind", "/", "/"]
    tmpfs = argv.index("--tmpfs")
    bind_root = argv.index(str(root.resolve()))
    protected = argv.index(str((root / ".git").resolve()))
    assert tmpfs < bind_root < protected  # 后面的挂载覆盖前面的挂载
    assert argv[protected - 1] == "--ro-bind" and str(cache.resolve()) in argv
    assert "--unshare-net" in argv and "--unshare-pid" in argv and "--die-with-parent" in argv
    assert argv[-6:] == ["--chdir", str(root.resolve()), "--", "/bin/sh", "-c", "make test"]
    assert "--unshare-net" not in Sandbox(network=True).argv("true", root, environ={})


def test_argv_unsets_secret_environment_variables(tmp_path):
    environ = {"OPENAI_API_KEY": "x", "GITHUB_TOKEN": "x", "DB_PASSWORD": "x",
               "LLM_CREDENTIALS": "x", "MY_LLM": "x", "PATH": "/bin", "HOME": "/root"}
    argv = Sandbox(hidden_env=("MY_LLM",)).argv("true", tmp_path, environ=environ)
    removed = {argv[i + 1] for i, value in enumerate(argv) if value == "--unsetenv"}
    assert removed == {"OPENAI_API_KEY", "GITHUB_TOKEN", "DB_PASSWORD", "LLM_CREDENTIALS", "MY_LLM"}


def test_protected_paths_cover_context_files_in_extra_writable_dirs(tmp_path):
    root, state = tmp_path / "work", tmp_path / "state"
    root.mkdir()
    state.mkdir()
    session = state / "session.jsonl"
    session.write_text("{}\n", encoding="utf-8")
    sandbox = Sandbox(protected=(".git", str(session), str(tmp_path / "elsewhere.md")),
                      writable=(str(state),))
    paths = sandbox.protected_paths(root)
    assert session.resolve() in paths  # 在额外可写目录里，必须改回只读
    assert (tmp_path / "elsewhere.md").resolve() not in paths  # 本来就只读，不用挂载
    argv = sandbox.argv("true", root, environ={})
    assert argv.index(str(state.resolve())) < argv.index(str(session.resolve()))
    assert argv[argv.index(str(session.resolve())) - 1] == "--ro-bind"


def test_protected_paths_ignore_outside_and_skip_missing_mounts(tmp_path):
    (tmp_path / ".git").mkdir()
    sandbox = Sandbox(protected=(".git", ".minicode", "/etc", "../outside"))
    root = tmp_path.resolve()
    assert sandbox.protected_paths(tmp_path) == [root / ".git", root / ".minicode"]
    argv = sandbox.argv("true", tmp_path, environ={})
    assert str(root / ".git") in argv and str(root / ".minicode") not in argv


def test_write_and_edit_share_protected_paths(tmp_path):
    (tmp_path / ".git").mkdir()
    config = tmp_path / ".git" / "config"
    config.write_text("[core]\n", encoding="utf-8")
    (tmp_path / "alias").symlink_to(tmp_path / ".git", target_is_directory=True)
    tools = runner(tmp_path, Sandbox())
    assert invoke(tools, "Write", path=".git/config", content="x").is_error
    assert invoke(tools, "Write", path="alias/config", content="x").is_error  # 先解析符号链接
    assert invoke(tools, "Write", path=".minicode/new.txt", content="x").is_error  # 尚不存在也保护
    assert not invoke(tools, "Read", path=".git/config").is_error  # 读取不受影响
    edit = invoke(tools, "Edit", path=".git/config", old="core", new="evil")
    assert edit.is_error and "受保护路径" in edit.content
    assert config.read_text() == "[core]\n" and not (tmp_path / ".minicode").exists()
    assert not invoke(tools, "Write", path=".gitignore", content="x").is_error  # 只按路径层级比较
    assert not invoke(tools, "Write", path=".git/../ok.txt", content="x").is_error


def test_bash_spec_describes_rules_without_mutating_original_tools():
    original = build_coding_tools()
    wrapped = {tool.spec.name: tool for tool in sandbox_tools(original, Sandbox())}
    plain = {tool.spec.name: tool for tool in original}
    description = wrapped["Bash"].spec.description
    assert "bubblewrap 沙箱" in description and "网络已断开" in description
    assert "不是沙箱" not in description  # 不保留与沙箱规则矛盾的原说明
    assert "bubblewrap" not in plain["Bash"].spec.description
    assert wrapped["Read"] is plain["Read"] and wrapped["Bash"].spec.parameters == plain["Bash"].spec.parameters


@posix_only
def test_bash_wrapper_keeps_quoting_and_timeout(tmp_path):
    log = tmp_path / "argv.log"
    fake = fake_bwrap(tmp_path / "fake-bwrap", log)
    work = tmp_path / "work"
    work.mkdir()
    tools = runner(work, Sandbox(executable=fake))
    result = invoke(tools, "Bash", command="printf '%s' \"a b\" '$HOME'")
    assert not result.is_error and result.content.startswith("a b$HOME")
    assert log.read_text().splitlines()[-1] == "printf '%s' \"a b\" '$HOME'"
    slow = invoke(tools, "Bash", command="sleep 5", timeout=0.1)
    assert slow.is_error and "超时" in slow.content
    unchecked = invoke(runner(work, Sandbox()), "Bash", command="echo hi")
    assert unchecked.is_error and "check()" in unchecked.content  # 不按 PATH 查找 bwrap


@posix_only
def test_check_pins_trusted_bwrap_outside_writable_roots(tmp_path, monkeypatch):
    work, log = tmp_path / "work", tmp_path / "calls.log"
    planted_dir, trusted_dir = work / ".venv" / "bin", tmp_path / "usr-bin"
    trusted = fake_bwrap(trusted_dir / "bwrap", log, "trusted")
    planted_dir.mkdir(parents=True)
    monkeypatch.setattr("minicode.sandbox.sys.platform", "linux")
    # 和 uv run 一样，工作区里的 .venv/bin 排在 PATH 最前面。
    monkeypatch.setenv("PATH", os.pathsep.join([str(planted_dir), str(trusted_dir), os.environ["PATH"]]))
    fake_bwrap(planted_dir / "bwrap", log, "planted-before-check")
    ready = Sandbox().check(work)
    assert ready.executable == str(Path(trusted).resolve())
    fake_bwrap(planted_dir / "bwrap", log, "planted-after-check")  # 模拟沙箱内的命令植入同名程序
    result = invoke(runner(work, ready), "Bash", command="echo hi")
    assert not result.is_error and result.content.startswith("hi")
    calls = [line for line in log.read_text().splitlines() if not line.startswith("-")]
    assert "trusted" in calls and not any(line.startswith("planted") for line in calls)
    # 指向工作区的符号链接、或只在工作区里找得到，都不算可信的 bwrap。
    linked = tmp_path / "linked-bin"
    linked.mkdir()
    (linked / "bwrap").symlink_to(planted_dir / "bwrap")
    monkeypatch.setenv("PATH", os.pathsep.join([str(linked), str(planted_dir)]))
    with pytest.raises(SandboxUnavailable, match="找不到可信的 bwrap"):
        Sandbox().check(work)


def test_check_fails_closed_with_reason(tmp_path, monkeypatch):
    monkeypatch.setattr("minicode.sandbox.sys.platform", "darwin")
    with pytest.raises(SandboxUnavailable, match="只支持 Linux"):
        Sandbox().check(tmp_path)
    monkeypatch.setattr("minicode.sandbox.sys.platform", "linux")
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(SandboxUnavailable, match="找不到可信的 bwrap"):
        Sandbox(executable=str(tmp_path / "missing-bwrap")).check(work)
    if os.name == "posix":
        fake = fake_executable(tmp_path / "denied-bwrap", "echo 'No permissions to create new namespace' >&2\nexit 1\n")
        with pytest.raises(SandboxUnavailable, match="No permissions"):
            Sandbox(executable=fake).check(work)


def test_cli_sandbox_options_require_sandbox_and_fail_closed(tmp_path, monkeypatch, capsys):
    assert main(["--demo", "--cwd", str(tmp_path), "--sandbox-network"]) == 2
    assert "需要同时指定 --sandbox" in capsys.readouterr().err

    def unavailable(self, root):
        raise SandboxUnavailable("测试环境禁止用户命名空间")
    monkeypatch.setattr(Sandbox, "check", unavailable)
    assert main(["--demo", "--cwd", str(tmp_path), "--sandbox"]) == 2
    assert "测试环境禁止用户命名空间" in capsys.readouterr().err


def test_cli_sandbox_protects_context_files(tmp_path, monkeypatch, capsys):
    (tmp_path / "NOTES.md").write_text("项目说明", encoding="utf-8")
    model = FakeModel([call("Write", path="NOTES.md", content="改写说明"),
                       call("Write", path="ok.txt", content="ok"), "done"])
    model.source, model.close = {}, lambda: None
    monkeypatch.setattr("minicode.cli.create_model", lambda config: model)
    monkeypatch.setattr(Sandbox, "check", lambda self, root: self)
    monkeypatch.setenv("MINICODE_API_KEY", "fake-test-key")
    assert main(["--model", "test", "--cwd", str(tmp_path), "--sandbox", "--instructions", "NOTES.md",
                 "--allow-tool", "Write", "--json", "-p", "task"]) == 0
    results = [e for e in map(json.loads, capsys.readouterr().out.splitlines()) if e["type"] == "tool_result"]
    assert [r["is_error"] for r in results] == [True, False]
    assert (tmp_path / "NOTES.md").read_text() == "项目说明" and (tmp_path / "ok.txt").exists()
    bash = next(t for t in model.requests[0]["tools"] if t.name == "Bash")
    assert "NOTES.md" in bash.description and "网络已断开" in bash.description


def test_cli_protects_session_inside_extra_writable_dir(tmp_path, monkeypatch, capsys):
    work, state = tmp_path / "work", tmp_path / "state"
    work.mkdir()
    state.mkdir()
    seen = []
    monkeypatch.setattr(Sandbox, "check", lambda self, root: seen.append((self, root)) or self)
    session = state / "session.jsonl"
    assert main(["--demo", "--json", "--cwd", str(work), "--session", str(session),
                 "--sandbox", "--sandbox-writable", str(state)]) == 0
    capsys.readouterr()
    sandbox, root = seen[0]
    assert session.resolve() in sandbox.protected_paths(root)


@pytest.fixture
def real_sandbox(tmp_path):
    sandbox = Sandbox()
    try:
        return sandbox.check(tmp_path)
    except SandboxUnavailable as error:
        pytest.skip(str(error))


def test_real_sandbox_limits_writes(real_sandbox, tmp_path):
    root = tmp_path / "work"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    tools = runner(root, real_sandbox)
    assert not invoke(tools, "Bash", command="echo hi > inside.txt").is_error
    assert (root / "inside.txt").read_text() == "hi\n"
    invoke(tools, "Bash", command="echo x > ../outside.txt")  # 写进沙箱自己的 /tmp
    assert not (tmp_path / "outside.txt").exists()
    probe = Path.home() / f".minicode-sandbox-test-{uuid4().hex}"
    try:
        home = invoke(tools, "Bash", command=f"echo x > {shlex.quote(str(probe))}")
        assert home.is_error and "Read-only file system" in home.content and not probe.exists()
    finally:
        if probe.exists():
            probe.unlink()
    git = invoke(tools, "Bash", command="echo x > .git/config")
    assert git.is_error and "Read-only file system" in git.content
    assert invoke(tools, "Bash", command="mv .git moved").is_error
    assert (root / ".git" / "config").read_text() == "[core]\n"


def test_real_sandbox_network_environment_and_extra_writable(real_sandbox, tmp_path, monkeypatch):
    root, cache = tmp_path / "work", tmp_path / "cache"
    root.mkdir()
    cache.mkdir()
    monkeypatch.setenv("FAKE_API_KEY", "sk-test")
    monkeypatch.setenv("MINICODE_VISIBLE", "visible")
    (cache / "session.jsonl").write_text("{}\n", encoding="utf-8")
    sandbox = Sandbox(protected=(".git", str(cache / "session.jsonl")), writable=(str(cache),))
    tools = runner(root, sandbox.check(root))
    python = shlex.quote(sys.executable)
    network = invoke(tools, "Bash", command=f"{python} -c \"import socket; "
                                            "socket.create_connection(('192.0.2.1', 80), timeout=2)\"")
    assert network.is_error and "unreachable" in network.content.lower()
    env = invoke(tools, "Bash", command="echo ${FAKE_API_KEY:-removed} $MINICODE_VISIBLE")
    assert env.content.splitlines()[0] == "removed visible"
    assert not invoke(tools, "Bash", command=f"echo c > {shlex.quote(str(cache / 'f'))}").is_error
    assert (cache / "f").read_text() == "c\n"
    session = invoke(tools, "Bash", command=f"echo x > {shlex.quote(str(cache / 'session.jsonl'))}")
    assert session.is_error and "Read-only file system" in session.content
    assert (cache / "session.jsonl").read_text() == "{}\n"


def test_real_sandbox_ends_background_processes(real_sandbox, tmp_path):
    tools = runner(tmp_path, real_sandbox)
    python = shlex.quote(sys.executable)
    late = (f"{python} -c \"import os, time; os.setsid(); open('started.txt', 'w').close(); "
            "time.sleep(1.0); open('late.txt', 'w').close()\" &"
            " while [ ! -f started.txt ]; do sleep 0.01; done")
    assert not invoke(tools, "Bash", command=late).is_error  # 命令先结束
    timed_out = invoke(tools, "Bash", command=late.replace("late.txt", "escaped.txt") + "; sleep 10",
                       timeout=0.5)
    assert timed_out.is_error and "超时" in timed_out.content
    time.sleep(1.0)
    assert (tmp_path / "started.txt").exists()
    assert not (tmp_path / "late.txt").exists()      # 正常结束时清理
    assert not (tmp_path / "escaped.txt").exists()   # setsid 也逃不出 PID 命名空间
