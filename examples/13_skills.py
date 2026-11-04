"""运行：uv run python examples/13_skills.py"""
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.agent import Agent
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
    model = FakeModel([call("Skill", name="review"), "已加载评审步骤，现在检查差异。"])
    runner = ToolRunner([library.tool()], ToolContext(root))
    agent = Agent(model, runner, "可用技能：\n" + library.catalog())
    print(list(agent.run("评审修改"))[-1]["text"])
