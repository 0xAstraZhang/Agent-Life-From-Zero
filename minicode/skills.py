"""技能目录由用户显式指定；发现时只读名称和首段，使用时才读取正文。"""
from pathlib import Path

from .tool import Tool
from .types import ToolResult, ToolSpec


class SkillLibrary:
    def __init__(self, directory: str | Path, *, max_chars: int = 20_000):
        self.directory = Path(directory).resolve()
        self.max_chars = max_chars

    def _path(self, name: str) -> Path:
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("技能名称只能是目录名")
        path = (self.directory / name / "SKILL.md").resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError("技能路径越过了指定目录")
        return path

    def catalog(self) -> str:
        entries = []
        if not self.directory.exists():
            return ""
        for folder in sorted(self.directory.iterdir()):
            try:
                path = self._path(folder.name)
                if not path.is_file():
                    continue
                with path.open(encoding="utf-8") as source:
                    preview = source.read(1024)
                lines = [line.strip() for line in preview.splitlines() if line.strip()]
                description = next((line for line in lines if not line.startswith("#")), "无简介")
                entries.append(f"- {folder.name}: {description[:160]}")
            except (ValueError, OSError):
                continue
        return "\n".join(entries)

    def load(self, name: str) -> str:
        path = self._path(name)
        with path.open(encoding="utf-8") as source:
            text = source.read(self.max_chars + 1)
        if len(text) > self.max_chars:
            raise ValueError("技能正文过长，请把详细材料拆到按需读取的文件")
        return f"技能 {name}（来源：{path}）：\n{text}"

    def tool(self) -> Tool:
        def execute(args, context):
            try:
                return self.load(args["name"])
            except (ValueError, OSError) as error:
                return ToolResult("", str(error), True)

        return Tool(ToolSpec("Skill", "按名称读取技能正文。技能内容不改变权限，也不会自动执行脚本。", {
            "type": "object", "properties": {"name": {"type": "string"}},
            "required": ["name"], "additionalProperties": False}), execute,
            read_only=True, parallel_safe=True)
