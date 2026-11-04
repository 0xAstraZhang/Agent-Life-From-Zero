"""工具声明与执行分开；协议适配器只接触 ToolSpec。"""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .types import ToolCall, ToolResult, ToolSpec


@dataclass
class ToolContext:
    cwd: Path


@dataclass
class Tool:
    spec: ToolSpec
    execute: Callable[[dict, ToolContext], str | ToolResult]
    read_only: bool = False
    parallel_safe: bool = False


class ToolRunner:
    def __init__(self, tools: list[Tool], context: ToolContext):
        self.context = context
        self.tools = {tool.spec.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("工具名称不能重复")
        for tool in tools:
            Draft202012Validator.check_schema(tool.spec.parameters)

    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self.tools.values()]

    def run(self, call: ToolCall) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolResult(call.id, f"未知工具：{call.name}", True)
        if call.parse_error or not isinstance(call.arguments, dict):
            return ToolResult(call.id, f"参数不是合法 JSON 对象：{call.parse_error or call.raw_arguments}", True)
        try:
            Draft202012Validator(tool.spec.parameters).validate(call.arguments)
        except ValidationError as error:
            return ToolResult(call.id, f"参数校验失败：{error.message}", True)
        try:
            output = tool.execute(call.arguments, self.context)
            if isinstance(output, ToolResult):
                return ToolResult(call.id, output.content, output.is_error)
            return ToolResult(call.id, str(output))
        except Exception as error:
            return ToolResult(call.id, f"{type(error).__name__}: {error}", True)
