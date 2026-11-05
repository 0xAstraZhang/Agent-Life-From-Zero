"""只读、串行委派。父子共享请求预算，历史分别保存。"""
from dataclasses import dataclass, field
from threading import Lock
from uuid import uuid4

from .agent import Agent
from .planning import readonly_runner
from .tool import Tool
from .types import ToolResult, ToolSpec


@dataclass
class RequestBudget:
    limit: int
    used: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self):
        if self.limit < 1 or self.used < 0:
            raise ValueError("请求预算必须为正数，已用额度不能为负数")

    def take(self):
        with self._lock:
            if self.used >= self.limit:
                raise RuntimeError("父子 Agent 的共享模型请求预算已用完")
            self.used += 1


class BudgetModel:
    def __init__(self, inner, budget: RequestBudget):
        self.inner, self.budget = inner, budget
        self.deadline = getattr(inner, "deadline", None)

    def set_deadline(self, deadline):
        self.deadline = deadline
        setter = getattr(self.inner, "set_deadline", None)
        if setter:
            setter(deadline)

    def complete(self, messages, tools=()):
        self.budget.take()
        return self.inner.complete(messages, tools)

    def summary_complete(self, messages):
        self.budget.take()
        summarize = getattr(self.inner, "summary_complete", None)
        return summarize(messages) if summarize else self.inner.complete(messages, [])

    def stream(self, messages, tools=()):
        self.budget.take()
        yield from self.inner.stream(messages, tools)


def subagent_tool(model_factory, runner_factory, *, max_turns=6, budget=None, observer=None, parent_id="main") -> Tool:
    spec = ToolSpec("Delegate", "委派独立的只读调查任务；子 Agent 只返回摘要，不能修改文件或再次委派。", {
        "type": "object", "properties": {"task": {"type": "string", "minLength": 1}},
        "required": ["task"], "additionalProperties": False})

    def execute(args, context):
        runner = readonly_runner(runner_factory(context), exclude={"Delegate"})
        model = model_factory()
        if budget is not None:
            model = BudgetModel(model, budget)
        def observe(event):
            if observer:
                observer({"parent_id": parent_id, **event})

        child = Agent(model, runner, "你负责只读调查。给出结论、文件位置、证据和未验证事项。",
                      max_turns=max_turns, deadline=getattr(context, "deadline", None),
                      observer=observe if observer else None, agent_id="child-" + uuid4().hex)
        final = None
        for event in child.run(args["task"]):
            if event["type"] == "result":
                final = event
        if final is None:
            return ToolResult("", "子 Agent 没有终止结果", True)
        return ToolResult("", final["text"][:12_000], final["status"] != "success")

    return Tool(spec, execute, read_only=True, parallel_safe=False)
