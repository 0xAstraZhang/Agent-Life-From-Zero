"""协议与流式练习共用的加法工具。"""
from pathlib import Path

from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec


def addition_runner():
    spec = ToolSpec("add", "把两个整数相加", {
        "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False,
    })
    return ToolRunner([Tool(spec, lambda args, ctx: str(args["a"] + args["b"]),
                           read_only=True)], ToolContext(Path.cwd()))
