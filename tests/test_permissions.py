from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

from minicode.permissions import Approval, PermissionRunner
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolCall, ToolSpec


def setup_runner(tmp_path, seen):
    schema = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}
    return ToolRunner([
        Tool(ToolSpec("Read", "read", schema), lambda args, _: args["value"], read_only=True, parallel_safe=True),
        Tool(ToolSpec("Write", "write", schema), lambda args, _: seen.append(args["value"]) or "ok"),
    ], ToolContext(tmp_path))


def test_default_deny_and_explicit_allow(tmp_path):
    seen = []
    inner = setup_runner(tmp_path, seen)
    denied = PermissionRunner(inner)
    assert denied.run(ToolCall("1", "Write", {"value": "x"})).is_error
    assert seen == []
    allowed = PermissionRunner(inner, allowed=["Write"])
    assert not allowed.run(ToolCall("2", "Write", {"value": "x"})).is_error
    assert seen == ["x"]
    assert not denied.run(ToolCall("3", "Read", {"value": "read"})).is_error


def test_readonly_cannot_be_overridden_by_allow_or_approval(tmp_path):
    seen = []
    runner = PermissionRunner(setup_runner(tmp_path, seen), Approval(lambda *_: True),
                              read_only=True, allowed=["Write"])
    assert [spec.name for spec in runner.specs()] == ["Read"]
    assert runner.run(ToolCall("1", "Write", {"value": "x"})).is_error
    assert seen == []


def test_invalid_args_do_not_prompt(tmp_path):
    asked = []
    runner = PermissionRunner(setup_runner(tmp_path, []), Approval(lambda *args: asked.append(args) or True))
    assert runner.run(ToolCall("1", "Write", {"value": 42})).is_error
    assert asked == []


def test_shared_approval_serializes_multiple_runners(tmp_path):
    state = {"active": 0, "max": 0}
    lock = Lock()

    def ask(*_):
        with lock:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return True

    approval = Approval(ask)
    runners = [PermissionRunner(setup_runner(tmp_path, []), approval) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda runner: runner.run(ToolCall("1", "Write", {"value": "x"})), runners))
    assert all(not result.is_error for result in results)
    assert state["max"] == 1
