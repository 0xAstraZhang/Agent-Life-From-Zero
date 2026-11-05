"""第 12 章：子任务独立读文件，结果经过父工具往返返回。"""
from lessons.ch12 import delegate_tool
from minicode.agent import Agent
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec, message_text


def test_child_reads_file_and_returns_result_to_parent(tmp_path):
    (tmp_path / "note.txt").write_text("pytest -x", encoding="utf-8")
    read = Tool(ToolSpec("ReadNote", "读取记录", {"type": "object"}),
                lambda args, context: (context.cwd / "note.txt").read_text(), read_only=True)
    child_model = FakeModel([call("ReadNote"), "测试命令是 pytest -x。"])
    child_runner = ToolRunner([read], ToolContext(tmp_path))
    delegate = delegate_tool(child_model, child_runner)
    parent_model = FakeModel([call("Delegate", task="读取记录并报告测试命令"), "收到调查结果。"])
    parent = Agent(parent_model, ToolRunner([delegate], ToolContext(tmp_path)))
    list(parent.run("父任务的私人细节不要复制给子任务"))
    assert [message.role for message in child_model.requests[0]["messages"]] == ["system", "user"]
    assert "私人细节" not in message_text(child_model.requests[0]["messages"][1])
    assert child_model.requests[1]["messages"][-1].content[0].content == "pytest -x"
    assert parent_model.requests[1]["messages"][-1].content[0].content == "测试命令是 pytest -x。"


def test_checkpoints(capsys):
    import runpy
    from pathlib import Path
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/12_subagents.py"))["main"]
    for step in ("minimal", "enhanced"):
        main(["--offline", "--step", step])
        output = capsys.readouterr().out
        assert "子 Agent 读取 project.txt" in output and "success" in output
        if step == "enhanced":
            assert "父子合计模型请求： 4" in output


def test_child_claim_without_reading_fails_checkpoint(monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path
    import runpy
    import pytest
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/12_subagents.py"))["main"]

    @contextmanager
    def skips_child_read(args, script):
        if script[0].name == "ReadNote":
            yield FakeModel(["测试命令是 python -m pytest。"])
        else:
            yield FakeModel(script)

    monkeypatch.setitem(main.__globals__, "open_model", skips_child_read)
    with pytest.raises(RuntimeError, match="没有完成实际读取"):
        main(["--offline"])
