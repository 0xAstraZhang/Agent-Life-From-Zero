"""第 12 章最小委派：工具函数里面运行另一个 Agent。"""
from minicode.agent import Agent
from minicode.tool import Tool
from minicode.types import ToolResult, ToolSpec


def delegate_tool(model, runner):
    def execute(args, context):
        child = Agent(model, runner, "先读取项目记录，再给出结论和证据。", max_turns=4)
        final = None
        for event in child.run(args["task"]):
            if event["type"] == "result":
                final = event
        if final is None:
            return ToolResult("", "子 Agent 没有返回终止结果", True)
        return ToolResult("", final["text"], final["status"] != "success")

    spec = ToolSpec("Delegate", "委派一个读取项目记录的独立调查。", {
        "type": "object", "properties": {"task": {"type": "string"}},
        "required": ["task"], "additionalProperties": False,
    })
    return Tool(spec, execute, read_only=True)
