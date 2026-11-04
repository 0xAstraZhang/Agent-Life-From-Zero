"""本章测试只依赖本章及之前的模块。"""
from pathlib import Path

import pytest

from minicode.subagents import BudgetModel, RequestBudget, subagent_tool
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec


def make_runner(tmp_path, outputs=None):
    outputs = outputs if outputs is not None else []
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    read = Tool(ToolSpec("Read", "read", schema), lambda args, ctx: "file content", True, True)
    write = Tool(ToolSpec("Write", "write", schema), lambda args, ctx: outputs.append("write") or "ok")
    return ToolRunner([read, write], ToolContext(tmp_path))

def test_subagent_has_own_context_readonly_tools_and_no_nested_delegate(tmp_path):
    touched = []
    base = make_runner(tmp_path, touched)
    child_model = FakeModel([call("Write"), call("Read"), "evidence summary"])
    delegate = subagent_tool(lambda: child_model, lambda ctx: parent)
    parent = ToolRunner([*base.tools.values(), delegate], ToolContext(tmp_path))
    result = parent.run(call("Delegate", task="investigate"))
    assert result.content == "evidence summary" and not result.is_error
    assert touched == []
    assert [tool.name for tool in child_model.requests[0]["tools"]] == ["Read"]
    assert [m.role for m in child_model.requests[0]["messages"]] == ["system", "user"]

def test_shared_request_budget_counts_parent_and_child():
    budget = RequestBudget(2)
    parent = BudgetModel(FakeModel(["parent"]), budget)
    child = BudgetModel(FakeModel(["child", "never"]), budget)
    parent.complete([])
    child.complete([])
    with pytest.raises(RuntimeError, match="预算"):
        list(child.stream([]))
    assert budget.used == 2
