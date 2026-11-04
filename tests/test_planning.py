"""本章测试只依赖本章及之前的模块。"""
from pathlib import Path

import pytest

from minicode.planning import TodoList, readonly_runner
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec


def make_runner(tmp_path, outputs=None):
    outputs = outputs if outputs is not None else []
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    read = Tool(ToolSpec("Read", "read", schema), lambda args, ctx: "file content", True, True)
    write = Tool(ToolSpec("Write", "write", schema), lambda args, ctx: outputs.append("write") or "ok")
    return ToolRunner([read, write], ToolContext(tmp_path))

def test_todo_requires_one_active_item_and_plan_blocks_writes(tmp_path):
    todo = TodoList()
    runner = ToolRunner([todo.tool()], ToolContext(tmp_path))
    result = runner.run(call("TodoWrite", items=[{"task": "a", "status": "in_progress"},
                                                {"task": "b", "status": "in_progress"}]))
    assert result.is_error and todo.items == []
    result = runner.run(call("TodoWrite", items=[{"task": "a", "status": "done"}]))
    assert not result.is_error and todo.items[0]["status"] == "done"
    read_only = readonly_runner(make_runner(tmp_path))
    assert [spec.name for spec in read_only.specs()] == ["Read"]
    assert read_only.run(call("Write")).is_error
