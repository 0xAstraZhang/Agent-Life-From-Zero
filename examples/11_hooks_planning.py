"""第 11 章：日志回调和待办状态各有独立检查点。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.ch11 import LoggingRunner, todo_tool
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.hooks import HookRunner
from minicode.permissions import PermissionRunner
from minicode.planning import TodoList
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec


def main(argv=None):
    args = parse_args("第 11 章：观察工具执行，或更新计划", argv,
                      steps=("minimal", "enhanced", "todo", "todo-enhanced"))
    with TemporaryDirectory() as directory:
        root = Path(directory)
        records, items = [], []
        context = ToolContext(root)
        if args.step.startswith("todo"):
            todo = TodoList()
            tool = todo_tool(items) if args.step == "todo" else todo.tool()
            runner = PermissionRunner(ToolRunner([tool], context), allowed=("TodoWrite",))
            prompt = "请调用 TodoWrite，列出‘定位问题’和‘运行测试’两步计划，第一项进行中。然后简短说明计划。"
            script = [call("TodoWrite", items=[{"task": "定位问题", "status": "in_progress"},
                                               {"task": "运行测试", "status": "pending"}]), "计划已记录。"]
        else:
            (root / "project.txt").write_text("项目使用 python -m pytest 运行测试。", encoding="utf-8")
            read = Tool(ToolSpec("ReadNote", "读取项目记录。", {
                "type": "object", "properties": {}, "additionalProperties": False}),
                lambda args, context: (context.cwd / "project.txt").read_text(encoding="utf-8"), read_only=True)
            inner = PermissionRunner(ToolRunner([read], context), read_only=True)

            def after(request, result):
                records.append((request.name, result.is_error))

            runner = LoggingRunner(inner, after) if args.step == "minimal" else HookRunner(inner, after=after)
            prompt = "请先调用 ReadNote，然后说明项目怎样测试。"
            script = [call("ReadNote"), "运行 python -m pytest。"]
        with open_model(args, script) as model:
            run_events(Agent(model, runner).run(prompt))
        if args.step.startswith("todo"):
            current = items if args.step == "todo" else todo.items
            if len(current) < 2 or not any(item["status"] == "in_progress" for item in current):
                raise RuntimeError("模型没有通过 TodoWrite 写入两步计划")
            print("当前计划：", current)
        else:
            if ("ReadNote", False) not in records:
                raise RuntimeError("没有记录到成功的 ReadNote 执行")
            print("执行记录：", records)


if __name__ == "__main__":
    main()
