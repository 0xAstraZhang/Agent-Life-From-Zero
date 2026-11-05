import os
from pathlib import Path
import re
import shlex
import sys
import time

import pytest

from minicode.coding_tools import MAX_OUTPUT, build_coding_tools
from minicode.agent import TaskTimeout
from minicode.permissions import PermissionRunner
from minicode.processes import ProcessManager
from minicode.sandbox import Sandbox, sandbox_tools
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall


def invoke(executor, name, **args):
    return executor.run(ToolCall("process", name, args))


def python(command):
    return shlex.join([sys.executable, "-u", "-c", command])


def session_id(result):
    assert not result.is_error, result.content
    return re.search(r"session_id=([0-9a-f]+)", result.content).group(1)


def test_long_sync_output_is_fully_readable_by_opaque_log_id(tmp_path):
    with ProcessManager() as manager:
        executor = ToolRunner(build_coding_tools(process_manager=manager), ToolContext(tmp_path))
        result = invoke(executor, "Bash", command=python("print('a' * 40000 + 'MIDDLE' + 'z' * 40000)"))
        assert not result.is_error and len(result.content) < MAX_OUTPUT + 200
        assert "MIDDLE" not in result.content
        log_id = re.search(r"log_id=([0-9a-f]+)", result.content).group(1)
        log = invoke(executor, "BashLog", log_id=log_id, offset=40000, limit=6)
        assert log.content.startswith("MIDDLE\n")
        assert "next_offset=40006" in log.content
        assert str(tmp_path) not in result.content
        assert invoke(executor, "BashLog", log_id="../secret").is_error


def test_log_pagination_does_not_split_utf8_characters(tmp_path):
    with ProcessManager() as manager:
        context = ToolContext(tmp_path)
        session = manager.start(python("print('中文' * 10000, end='')"), context, interactive=False)
        session.process.wait(timeout=2)
        pages, offset = [], 0
        for _ in range(4):
            result = manager.read_log(session.id, context, offset=offset)
            pages.append(result.content.rsplit("\n[", 1)[0])
            offset = int(re.search(r"next_offset=(\d+)", result.content).group(1))
            if offset == session.path.stat().st_size:
                break
        assert "".join(pages) == "中文" * 10000


@pytest.mark.skipif(os.name != "posix", reason="POSIX nonblocking pipe input")
def test_background_session_accepts_stdin_and_supports_polling(tmp_path):
    with ProcessManager() as manager:
        executor = ToolRunner(build_coding_tools(process_manager=manager), ToolContext(tmp_path))
        command = python("import sys; print('READY', flush=True); print('reply:' + sys.stdin.readline().strip())")
        identifier = session_id(invoke(executor, "BashStart", command=command))
        sent = invoke(executor, "BashSend", session_id=identifier, text="hello\n", eof=True)
        assert not sent.is_error
        result = invoke(executor, "BashPoll", session_id=identifier, wait=2)
        assert not result.is_error and "READY" in result.content and "reply:hello" in result.content
        assert "退出码 0" in result.content
        assert invoke(executor, "BashSend", session_id=identifier, text="late").is_error


def test_background_timeout_and_stop(tmp_path):
    with ProcessManager() as manager:
        executor = ToolRunner(build_coding_tools(process_manager=manager), ToolContext(tmp_path))
        identifier = session_id(invoke(executor, "BashStart", command=python("import time; time.sleep(30)"), timeout=0.05))
        result = invoke(executor, "BashPoll", session_id=identifier, wait=2)
        assert result.is_error and "超时" in result.content
        identifier = session_id(invoke(executor, "BashStart", command=python("import time; time.sleep(30)")))
        assert not invoke(executor, "BashStop", session_id=identifier).is_error
        assert "已终止" in invoke(executor, "BashPoll", session_id=identifier).content


def test_task_deadline_terminates_sync_and_background_processes(tmp_path):
    with ProcessManager() as manager:
        context = ToolContext(tmp_path, deadline=time.monotonic() + 0.1)
        executor = ToolRunner(build_coding_tools(process_manager=manager), context)
        started = time.monotonic()
        result = invoke(executor, "Bash", command=python("import time; time.sleep(30)"), timeout=30)
        assert result.is_error and "超时" in result.content
        assert time.monotonic() - started < 2
        context.deadline = time.monotonic() + 0.1
        identifier = session_id(invoke(executor, "BashStart", command=python("import time; time.sleep(30)")))
        process = manager._sessions[identifier].process
        process.wait(timeout=2)
        assert process.returncode != 0


def test_close_kills_running_process_and_revokes_log_handles(tmp_path):
    manager = ProcessManager()
    context = ToolContext(tmp_path)
    session = manager.start(python("import time; time.sleep(30)"), context)
    log_path = session.path
    manager.close()
    manager.close()
    assert session.process.poll() is not None and not log_path.exists()
    with pytest.raises(ValueError, match="未知"):
        manager.read_log(session.id, context)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_close_terminates_process_group_descendants(tmp_path):
    manager = ProcessManager()
    child = python("import time; time.sleep(0.4); open('late.txt', 'w').write('escaped')")
    session = manager.start(child + " & wait", ToolContext(tmp_path))
    manager.close()
    time.sleep(0.5)
    assert session.process.poll() is not None
    assert not (tmp_path / "late.txt").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_exited_background_shell_reaps_descendants_without_polling(tmp_path):
    with ProcessManager() as manager:
        child = python("import time; time.sleep(0.2); open('late.txt', 'w').write('escaped')")
        session = manager.start(child + " &", ToolContext(tmp_path))
        # Waiting on the raw process intentionally does not invoke Poll/Log or
        # _status. Its manager's reaper must clean up without those calls.
        session.process.wait(timeout=2)
        assert session.process.returncode == 0
        time.sleep(0.3)
        assert session.finished
        assert not (tmp_path / "late.txt").exists()


@pytest.mark.parametrize("failure", [KeyboardInterrupt, TaskTimeout])
@pytest.mark.parametrize("stage", ["session", "timer_start", "reaper_start"])
def test_interrupted_process_registration_cleans_up(tmp_path, monkeypatch, failure, stage):
    import minicode.processes as processes

    spawned = []
    original_popen = processes.subprocess.Popen

    def track_spawn(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        spawned.append(process)
        return process

    def interrupt(*args, **kwargs):
        raise failure("registration interrupted")

    monkeypatch.setattr(processes.subprocess, "Popen", track_spawn)
    if stage == "session":
        monkeypatch.setattr(processes, "ProcessSession", interrupt)
    elif stage == "timer_start":
        monkeypatch.setattr(processes.Timer, "start", interrupt)
    else:
        original_start = processes.Thread.start

        def start(thread):
            if thread.name.startswith("minicode-reap-"):
                raise failure("registration interrupted")
            original_start(thread)

        monkeypatch.setattr(processes.Thread, "start", start)
    manager = ProcessManager()
    with pytest.raises(failure, match="registration interrupted"):
        manager.start(python("import time; time.sleep(30)"), ToolContext(tmp_path))
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
    assert spawned[0].stdin.closed
    assert manager._sessions == {}
    assert not list(Path(manager._directory.name).iterdir())
    manager.close()


def test_session_access_checks_workspace_and_permissions(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with ProcessManager() as manager:
        tools = build_coding_tools(process_manager=manager)
        runner = ToolRunner(tools, ToolContext(tmp_path))
        restricted = PermissionRunner(runner, read_only=True)
        names = {tool.name for tool in restricted.specs()}
        assert not names.intersection({"BashStart", "BashPoll", "BashSend", "BashStop", "BashLog"})
        denied = invoke(PermissionRunner(runner), "BashStart", command="echo secret")
        assert denied.is_error and "未授权" in denied.content
        session = manager.start("echo one", ToolContext(tmp_path))
        with pytest.raises(ValueError, match="当前工作区"):
            manager.read_log(session.id, ToolContext(other))


@pytest.mark.skipif(os.name != "posix", reason="shell fixture requires POSIX")
def test_background_start_uses_same_sandbox_wrapper(tmp_path):
    marker = tmp_path / "sandbox.log"
    executable = tmp_path / "bwrap"
    executable.write_text("#!/bin/sh\nprintf 'wrapped' > " + shlex.quote(str(marker)) +
                          '\nwhile [ "$1" != "--" ]; do shift; done\nshift\nexec "$@"\n')
    executable.chmod(0o755)
    with ProcessManager() as manager:
        tools = sandbox_tools(build_coding_tools(process_manager=manager), Sandbox(executable=str(executable)))
        executor = ToolRunner(tools, ToolContext(tmp_path))
        identifier = session_id(invoke(executor, "BashStart", command="echo sandboxed"))
        result = invoke(executor, "BashPoll", session_id=identifier, wait=2)
        assert not result.is_error and "sandboxed" in result.content
        assert marker.read_text() == "wrapped"
        assert "bubblewrap" in executor.tools["BashStart"].spec.description
