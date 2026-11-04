"""运行：uv run python examples/09_context.py"""
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.agent import Agent
from minicode.context import build_system_prompt, load_project_instructions
from minicode.testing import FakeModel
from minicode.tool import ToolContext, ToolRunner
from minicode.types import message_text

with TemporaryDirectory() as directory:
    root = Path(directory)
    (root / "MINICODE.md").write_text("使用 pytest 验证修改。", encoding="utf-8")
    model = FakeModel(["先读代码，修改后运行 pytest。"])
    prompt = build_system_prompt(root, load_project_instructions(root))
    agent = Agent(model, ToolRunner([], ToolContext(root)), prompt)
    final = list(agent.run("修复一个 bug"))[-1]
    assert "pytest" in message_text(model.requests[0]["messages"][0])
    print(final["text"])
