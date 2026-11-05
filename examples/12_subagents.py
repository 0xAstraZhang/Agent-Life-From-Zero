"""第 12 章：父 Agent 委派子 Agent 读取一份真实的项目记录。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.ch12 import delegate_tool
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.permissions import PermissionRunner
from minicode.subagents import BudgetModel, RequestBudget, subagent_tool
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec


def main(argv=None):
    args = parse_args("第 12 章：委派一次具体调查", argv)
    parent_script = [call("Delegate", task="读取项目记录，找出测试命令并注明证据。"), "根据 project.txt，运行 python -m pytest。"]
    child_script = [call("ReadNote"), "project.txt 记载：测试命令是 python -m pytest。"]
    with TemporaryDirectory() as directory, open_model(args, parent_script) as parent_model, open_model(args, child_script) as child_model:
        root = Path(directory)
        (root / "project.txt").write_text("项目名称：星河\n测试命令：python -m pytest\n", encoding="utf-8")
        context = ToolContext(root)
        reads = []

        def read_note(args, context):
            text = (context.cwd / "project.txt").read_text(encoding="utf-8")
            reads.append(text)
            print("子 Agent 读取 project.txt：", text.strip())
            return text

        read = Tool(ToolSpec("ReadNote", "读取 project.txt 中的项目记录。", {
            "type": "object", "properties": {}, "additionalProperties": False}), read_note, read_only=True)
        child_runner = PermissionRunner(ToolRunner([read], context), read_only=True)
        if args.step == "minimal":
            delegate = delegate_tool(child_model, child_runner)
        else:
            budget = RequestBudget(8)
            parent_model = BudgetModel(parent_model, budget)
            delegate = subagent_tool(lambda: child_model, lambda context: child_runner,
                                     max_turns=4, budget=budget)
        runner = PermissionRunner(ToolRunner([delegate], context), read_only=True)
        events = run_events(Agent(parent_model, runner, max_turns=4).run("请用 Delegate 调查项目怎样运行测试，收到证据后回答。"))
        returned = [event for event in events if event["type"] == "tool_result" and not event["is_error"]]
        if not reads or not any("python -m pytest" in event["content"] for event in returned):
            raise RuntimeError("委派没有完成实际读取并向父 Agent 返回测试命令")
        if args.step == "enhanced":
            print("父子合计模型请求：", budget.used)


if __name__ == "__main__":
    main()
