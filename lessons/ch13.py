"""第 13 章最小原型：固定练习目录的简介与正文分别读取。"""
from minicode.tool import Tool
from minicode.types import ToolSpec


class LessonSkills:
    def __init__(self, directory):
        self.paths = {path.parent.name: path for path in sorted(directory.glob("*/SKILL.md"))}

    def catalog(self):
        entries = []
        for name, path in self.paths.items():
            with path.open(encoding="utf-8") as source:
                for line in source:
                    description = line.strip()
                    if description and not description.startswith("#"):
                        entries.append(f"- {name}: {description}")
                        break
        return "\n".join(entries)

    def load(self, name):
        return self.paths[name].read_text(encoding="utf-8")


def skill_tool(library):
    spec = ToolSpec("Skill", "按名称读取技能的完整步骤。", {
        "type": "object", "properties": {"name": {"type": "string"}},
        "required": ["name"], "additionalProperties": False,
    })

    def execute(args, context):
        return library.load(args["name"])

    return Tool(spec, execute, read_only=True)
