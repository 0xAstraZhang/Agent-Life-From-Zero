"""第 15 章：先独立验收真实任务，再给同一任务加预算。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.runtime import open_model, parse_args
from minicode.agent import Agent
from minicode.budget import Budget, BudgetModel
from minicode.coding_tools import build_coding_tools
from minicode.permissions import PermissionRunner
from minicode.testing import call
from minicode.tool import ToolContext, ToolRunner


def evaluate(model, root):
    target = root / "answer.py"
    target.write_text("answer = 41\n", encoding="utf-8")
    runner = PermissionRunner(
        ToolRunner(build_coding_tools(include_bash=False), ToolContext(root)), allowed=["Edit"])
    events = list(Agent(model, runner, max_turns=6).run(
        "先读取 answer.py，再用 Edit 将 answer 的值从 41 改成 42，其他内容保持原样。"))
    final = events[-1]
    file_passed = target.read_text(encoding="utf-8") == "answer = 42\n"
    return {"task": "read_then_edit", "status": final["status"],
            "file_passed": file_passed,
            "passed": final["status"] == "success" and file_passed}


def main(argv=None):
    args = parse_args("运行一个真实修改任务并独立验收", argv)
    script = [call("Read", path="answer.py"),
              call("Edit", path="answer.py", old="41", new="42"), "完成"]
    with TemporaryDirectory() as directory, open_model(args, script) as model:
        if args.step == "enhanced":
            budget = Budget(token_limit=10_000)
            model = BudgetModel(model, budget)
        report = evaluate(model, Path(directory))
        if args.step == "enhanced":
            report["tokens"] = budget.usage.input_tokens + budget.usage.output_tokens
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
