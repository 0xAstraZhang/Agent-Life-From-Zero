"""第 2 章完整快照：增加工具注册与正式 Agent；尚无流式、会话或权限。"""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .ch01 import FakeModel, Message, ToolCall, ToolResult, ToolSpec, call, message_text, tool_calls


# 第一步：在第一章内部类型之外增加可执行工具，不改变模型看到的 ToolSpec。
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
    def __init__(self, tools, context):
        self.context = context
        self.tools = {tool.spec.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("工具名称不能重复")
        for tool in tools:
            Draft202012Validator.check_schema(tool.spec.parameters)

    def specs(self):
        return [tool.spec for tool in self.tools.values()]

    def run(self, request):
        tool = self.tools.get(request.name)
        if tool is None:
            return ToolResult(request.id, f"未知工具：{request.name}", True)
        if not isinstance(request.arguments, dict):
            return ToolResult(request.id, "参数必须是对象", True)
        try:
            Draft202012Validator(tool.spec.parameters).validate(request.arguments)
        except ValidationError as error:
            return ToolResult(request.id, f"参数校验失败：{error.message}", True)
        try:
            output = tool.execute(request.arguments, self.context)
            if isinstance(output, ToolResult):
                return ToolResult(request.id, output.content, output.is_error)
            return ToolResult(request.id, str(output))
        except Exception as error:
            return ToolResult(request.id, f"{type(error).__name__}: {error}", True)


# 第二步：将第一章 run 的消息列表搬到对象，把工具分支替换成 runner.run。
class Agent:
    def __init__(self, model, runner, system_prompt="你是一个编程助手。", *, max_turns=20):
        if max_turns < 1:
            raise ValueError("max_turns 必须至少为 1")
        self.model, self.runner = model, runner
        self.max_turns = max_turns
        self.history = [Message.text("system", system_prompt)]

    def run(self, prompt):
        self.history.append(Message.text("user", prompt))
        for turn in range(1, self.max_turns + 1):
            try:
                reply = self.model.complete(self.history, self.runner.specs())
            except Exception as error:
                yield self._result("error", str(error), turn)
                return
            if reply.stop_reason not in {"stop", "tool_calls"}:
                yield self._result(reply.stop_reason, message_text(reply.message), turn)
                return
            calls = tool_calls(reply.message)
            if bool(calls) != (reply.stop_reason == "tool_calls"):
                yield self._result("error", "停止原因与调用不一致", turn)
                return
            prior_ids = {c.id for message in self.history for c in tool_calls(message)}
            ids = [c.id for c in calls]
            if any(not value for value in ids) or len(set(ids)) != len(ids) or prior_ids.intersection(ids):
                yield self._result("error", "工具调用 ID 缺失或重复", turn)
                return
            self.history.append(reply.message)
            if reply.stop_reason == "stop":
                yield self._result("success", message_text(reply.message), turn)
                return
            for request in calls:
                result = self.runner.run(request)
                self.history.append(Message("tool", [result]))
                yield {"type": "tool_result", "result": result, "call_id": result.call_id,
                       "content": result.content, "is_error": result.is_error}
        yield self._result("max_turns", "已达到最大轮数", self.max_turns)

    @staticmethod
    def _result(status, text, turns):
        return {"type": "result", "status": status, "text": text, "turns": turns}


def main():
    add = Tool(ToolSpec("add", "计算两个整数的和", {
        "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False,
    }), lambda args, ctx: str(args["a"] + args["b"]), read_only=True, parallel_safe=True)
    model = FakeModel([call("add", a="2", b=3), call("add", a=2, b=3), "2 + 3 = 5"])
    agent = Agent(model, ToolRunner([add], ToolContext(Path.cwd())))
    for event in agent.run("2 + 3 是多少？"):
        print({key: value for key, value in event.items() if key != "result"})


if __name__ == "__main__":
    main()
