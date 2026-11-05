"""第 11 章：回调位于执行之后，待办工具更新可观察的状态。"""
from lessons.ch11 import LoggingRunner, todo_tool
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec


def test_after_observes_executed_result(tmp_path):
    events = []

    def execute(args, context):
        events.append("executed")
        return "文件内容"

    def after(request, result):
        events.append(result.content)

    tool = Tool(ToolSpec("Read", "读取", {"type": "object"}), execute)
    runner = LoggingRunner(ToolRunner([tool], ToolContext(tmp_path)), after)
    result = runner.run(call("Read"))
    assert events == ["executed", "文件内容"]
    assert result.content == "文件内容" and not result.is_error


def test_todo_state_changes_across_calls(tmp_path):
    items = []
    runner = ToolRunner([todo_tool(items)], ToolContext(tmp_path))
    runner.run(call("TodoWrite", items=[{"task": "验证", "status": "pending"}]))
    result = runner.run(call("TodoWrite", items=[{"task": "验证", "status": "done"}]))
    assert items == [{"task": "验证", "status": "done"}]
    assert '"done"' in result.content


def test_checkpoints(capsys):
    import runpy
    from pathlib import Path
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/11_hooks_planning.py"))["main"]
    for step in ("minimal", "enhanced", "todo", "todo-enhanced"):
        main(["--offline", "--step", step])
        output = capsys.readouterr().out
        assert "success" in output
        if step.startswith("todo"):
            assert "当前计划" in output
        else:
            assert "执行记录" in output


def test_checkpoint_requires_tool_effects(monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    import runpy
    import pytest
    from minicode.testing import FakeModel
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/11_hooks_planning.py"))["main"]

    @contextmanager
    def no_tools(args, script):
        yield FakeModel(["我已经做完了。"])

    monkeypatch.setitem(main.__globals__, "open_model", no_tools)
    for step in ("minimal", "enhanced", "todo", "todo-enhanced"):
        with pytest.raises(RuntimeError, match="没有"):
            main(["--offline", "--step", step])
