"""运行：uv run python examples/13_skills.py"""
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.hooks import HookRunner
from minicode.permissions import Approval, PermissionRunner
from minicode.skills import SkillLibrary
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner

with TemporaryDirectory() as directory:
    root = Path(directory)
    skill = root / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# 代码评审\n检查改动是否满足验收标准。\n\n先读差异，再查调用方，最后运行相关测试。", encoding="utf-8")
    library = SkillLibrary(root / "skills")
    print(library.catalog())
    context = ToolContext(root)
    tools = build_coding_tools(include_bash=False)
    approval = Approval()  # 无人值守时不批准写操作。
    records = []

    def before(request):
        records.append(("before", request.name))

    def after(request, result):
        records.append(("after", request.name, result.is_error))

    def assemble(current_tools):
        inner = ToolRunner(current_tools, context)
        permissions = PermissionRunner(inner, approval=approval, allowed=(), read_only=False)
        return HookRunner(permissions, before=before, after=after)

    runner = assemble(tools)
    assert runner.run(call("Write", path="blocked.txt", content="no")).is_error
    # 加工具只改变注册表；审批对象、授权配置和 hooks 一起保留下来。
    tools.append(library.tool())
    runner = assemble(tools)
    model = FakeModel([call("Skill", name="review"),
                       call("Write", path="blocked.txt", content="no"),
                       "已加载评审步骤，现在检查差异。"])
    agent = Agent(model, runner, "可用技能：\n" + library.catalog())
    print(list(agent.run("评审修改"))[-1]["text"])
    assert not (root / "blocked.txt").exists()
    assert ("after", "Skill", False) in records
    assert records.count(("after", "Write", True)) == 2
    print("写入仍被拒绝，hooks 继续记录 Skill 和 Write。")
