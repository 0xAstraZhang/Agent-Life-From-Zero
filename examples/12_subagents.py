"""运行：uv run python examples/12_subagents.py"""
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.agent import Agent
from minicode.subagents import BudgetModel, RequestBudget, subagent_tool
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner

with TemporaryDirectory() as directory:
    context = ToolContext(Path(directory))
    budget = RequestBudget(3)
    delegate = subagent_tool(lambda: FakeModel(["调查结果：项目使用 pytest。"]),
                             lambda ctx: ToolRunner([], ctx), budget=budget)
    model = BudgetModel(FakeModel([call("Delegate", task="调查测试方式"), "根据调查，下一步运行 pytest。"]), budget)
    agent = Agent(model, ToolRunner([delegate], context))
    print(list(agent.run("帮我确认如何验证修改"))[-1]["text"])
    assert budget.used == 3
    print(f"父子合计模型请求：{budget.used}")
