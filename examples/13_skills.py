"""第 13 章：先观察简介→读取正文，再验证集成后的权限与 hooks。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.ch13 import LessonSkills, skill_tool
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.hooks import HookRunner
from minicode.permissions import Approval, PermissionRunner
from minicode.skills import SkillLibrary
from minicode.testing import call
from minicode.tool import ToolContext, ToolRunner


def main(argv=None):
    args = parse_args("第 13 章：按需读取技能正文", argv)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        folder = root / "skills" / "review"
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "# 代码评审\n检查代码是否符合输入输出约定。\n\n"
            "## 步骤\n1. 用一组具体输入推演代码。\n2. 对照预期结果。\n3. 给出错误原因和最小修复。\n",
            encoding="utf-8")
        context = ToolContext(root)
        records = []
        if args.step == "minimal":
            library = LessonSkills(root / "skills")
            runner = PermissionRunner(ToolRunner([skill_tool(library)], context), read_only=True)
        else:
            library = SkillLibrary(root / "skills")
            tools = build_coding_tools(include_bash=False)
            approval = Approval()

            def after(request, result):
                records.append((request.name, result.is_error))

            def assemble(current_tools):
                inner = ToolRunner(current_tools, context)
                return HookRunner(PermissionRunner(inner, approval=approval), after=after)

            runner = assemble(tools)
            assert runner.run(call("Write", path="blocked.txt", content="no")).is_error
            tools.append(library.tool())
            runner = assemble(tools)
        catalog = library.catalog()
        print("第一次请求只附简介：\n" + catalog)
        script = [call("Skill", name="review"), "add(2, 3) 返回 -1，预期 5；应把 a - b 改为 a + b。"]
        with open_model(args, script) as model:
            agent = Agent(model, runner, "你是代码评审助手。先按需读取技能，再应用其中的步骤。\n可用技能：\n" + catalog)
            events = run_events(agent.run("请使用 review 技能评审这段加法函数：def add(a, b): return a - b。输入 2、3，预期输出 5。"))
        skill_ids = {call.id for event in events if event["type"] == "assistant"
                     for call in event.get("tool_calls", []) if call.name == "Skill"}
        body = (folder / "SKILL.md").read_text(encoding="utf-8")
        loaded = [event for event in events if event["type"] == "tool_result"
                  and event["call_id"] in skill_ids and not event["is_error"]]
        if not any(body in event["content"] for event in loaded):
            raise RuntimeError("模型没有通过 Skill 加载评审正文")
        if args.step == "enhanced":
            assert runner.run(call("Write", path="blocked.txt", content="no")).is_error
            assert not (root / "blocked.txt").exists()
            assert records.count(("Write", True)) >= 2
            print("加入 Skill 前后，写入均被拒绝；执行记录：", records)


if __name__ == "__main__":
    main()
