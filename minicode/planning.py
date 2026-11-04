"""计划是可检查的状态；计划模式由工具可用性落实。"""
import json

from .tool import Tool
from .types import ToolResult, ToolSpec


class TodoList:
    def __init__(self):
        self.items = []

    def tool(self) -> Tool:
        spec = ToolSpec("TodoWrite", "更新任务清单；最多一项 in_progress，完成状态应有验证依据。", {
            "type": "object", "properties": {"items": {"type": "array", "items": {
                "type": "object", "properties": {
                    "task": {"type": "string", "minLength": 1},
                    "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
                "required": ["task", "status"], "additionalProperties": False}}},
            "required": ["items"], "additionalProperties": False})

        def execute(args, context):
            if sum(item["status"] == "in_progress" for item in args["items"]) > 1:
                return ToolResult("", "同一时间只能有一项 in_progress", True)
            self.items = [dict(item) for item in args["items"]]
            return json.dumps(self.items, ensure_ascii=False)

        # 会修改内存中的待办状态；不把它标为只读，是否授权由调用方决定。
        return Tool(spec, execute, read_only=False, parallel_safe=False)


class ReadOnlyRunner:
    def __init__(self, inner, *, exclude=()):
        self.inner = inner
        self.context = inner.context
        self.tools = {name: tool for name, tool in inner.tools.items()
                      if tool.read_only and name not in set(exclude)}

    def specs(self):
        return [tool.spec for tool in self.tools.values()]

    def run(self, call):
        if call.name not in self.tools:
            return ToolResult(call.id, "当前只读模式不允许这个工具", True)
        return self.inner.run(call)


def readonly_runner(inner, *, exclude=()):
    return ReadOnlyRunner(inner, exclude=exclude)
