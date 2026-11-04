"""第 6 章离线演示：Read → Edit → Bash，所有操作在临时目录内。"""
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory

from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner

with TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "answer.py").write_text("answer = 41\n", encoding="utf-8")
    model = FakeModel([
        call("Read", path="answer.py"),
        call("Edit", path="answer.py", old="41", new="42"),
        call("Bash", command=f"{shlex.quote(sys.executable)} -c 'from answer import answer; assert answer == 42'"),
        "已把答案改成 42，断言通过。",
    ])
    agent = Agent(model, ToolRunner(build_coding_tools(), ToolContext(root)))
    for event in agent.run("修正 answer.py，并验证结果"):
        if event["type"] == "tool_result":
            print(event["content"])
        elif event["type"] == "result":
            print(event["status"], event["text"])
