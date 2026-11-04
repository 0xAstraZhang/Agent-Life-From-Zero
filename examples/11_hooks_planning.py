"""运行：uv run python examples/11_hooks_planning.py"""
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.agent import Agent
from minicode.hooks import HookRunner
from minicode.planning import TodoList
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner

with TemporaryDirectory() as directory:
    todo = TodoList()
    observed = []
    runner = HookRunner(ToolRunner([todo.tool()], ToolContext(Path(directory))),
                        after=lambda request, result: observed.append((request.name, result.is_error)))
    model = FakeModel([call("TodoWrite", items=[{"task": "定位问题", "status": "in_progress"}]), "计划已记录。"])
    agent = Agent(model, runner)
    print(list(agent.run("先列计划"))[-1]["text"])
    print(todo.items)
    print(observed)
