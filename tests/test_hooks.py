"""本章测试只依赖本章及之前的模块。"""
from pathlib import Path

import pytest

from minicode.hooks import HookRunner
from minicode.permissions import PermissionRunner
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolCall, ToolSpec


def make_runner(tmp_path, outputs=None):
    outputs = outputs if outputs is not None else []
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    read = Tool(ToolSpec("Read", "read", schema), lambda args, ctx: "file content", True, True)
    write = Tool(ToolSpec("Write", "write", schema), lambda args, ctx: outputs.append("write") or "ok")
    return ToolRunner([read, write], ToolContext(tmp_path))

def test_hook_rewrite_still_passes_permissions(tmp_path):
    touched = []
    runner = PermissionRunner(make_runner(tmp_path, touched))
    hooks = HookRunner(runner, before=lambda c: ToolCall(c.id, "Write", {}))
    result = hooks.run(call("Read"))
    assert result.is_error
    assert touched == []

def test_hooks_cannot_change_call_id_or_mask_failure(tmp_path):
    runner = make_runner(tmp_path)
    invalid = HookRunner(runner, before=lambda c: ToolCall("other", "Read", {}))
    assert invalid.run(call("Read")).is_error
    observe = HookRunner(runner, after=lambda c, r: setattr(r, "is_error", False))
    assert observe.run(call("Missing")).is_error
