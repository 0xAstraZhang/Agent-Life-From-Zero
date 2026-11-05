"""第 11 章：先看见回调位置，再把计划变成可更新的状态。"""
import json

from minicode.tool import Tool
from minicode.types import ToolSpec


class LoggingRunner:
    def __init__(self, inner, after):
        self.inner, self.after = inner, after
        self.tools, self.context = inner.tools, inner.context

    def specs(self):
        return self.inner.specs()

    def run(self, call):
        result = self.inner.run(call)
        self.after(call, result)
        return result


def todo_tool(items):
    def execute(args, context):
        items[:] = args["items"]
        return json.dumps(items, ensure_ascii=False)

    spec = ToolSpec("TodoWrite", "用完整任务清单更新计划。", {
        "type": "object", "properties": {"items": {"type": "array", "items": {
            "type": "object", "properties": {
                "task": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
            "required": ["task", "status"], "additionalProperties": False}}},
        "required": ["items"], "additionalProperties": False,
    })
    return Tool(spec, execute)
