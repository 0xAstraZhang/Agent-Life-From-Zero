from pathlib import Path

from lessons.ch14 import echo_process, echo_tool, main, run_agent
from minicode.testing import FakeModel
from minicode.tool import ToolContext


def test_minimal_bridge_uses_server_and_reaps_process(tmp_path):
    with echo_process() as process:
        tool = echo_tool(process)
        assert tool.execute({"text": "实际进程往返"}, ToolContext(tmp_path)) == "实际进程往返"
        assert tool.execute({"text": "第二次调用"}, ToolContext(tmp_path)) == "第二次调用"
    assert process.poll() is not None


def test_both_mcp_checkpoints():
    assert main(["--offline", "--step", "minimal"]) == 0
    assert main(["--offline", "--step", "enhanced"]) == 0


def test_claim_without_echo_is_not_a_completed_checkpoint(tmp_path):
    with echo_process() as process:
        assert run_agent(FakeModel(["已经调用了 echo。"]), [echo_tool(process)], tmp_path) == 1
