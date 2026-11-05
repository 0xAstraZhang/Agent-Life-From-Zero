"""第 15 章：在模型边界累计用量，父子 Agent 可以共享同一个预算。"""
from dataclasses import dataclass, field
from threading import Lock

from .types import Usage


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    token_limit: int = 50_000
    usage: Usage = field(default_factory=Usage)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def __post_init__(self):
        if self.token_limit <= 0:
            raise ValueError("token_limit 必须大于 0")

    def check(self):
        with self._lock:
            if self.usage.input_tokens + self.usage.output_tokens >= self.token_limit:
                raise BudgetExceeded("累计 token 已达到预算；没有发送下一次模型请求")

    def record(self, usage: Usage):
        with self._lock:
            self.usage.input_tokens += usage.input_tokens
            self.usage.output_tokens += usage.output_tokens


class BudgetModel:
    def __init__(self, inner, budget: Budget):
        self.inner, self.budget = inner, budget
        self.deadline = getattr(inner, "deadline", None)

    def set_deadline(self, deadline):
        self.deadline = deadline
        setter = getattr(self.inner, "set_deadline", None)
        if setter:
            setter(deadline)

    def complete(self, messages, tools=()):
        self.budget.check()
        reply = self.inner.complete(messages, tools)
        self.budget.record(reply.usage)
        return reply

    def summary_complete(self, messages):
        self.budget.check()
        summarize = getattr(self.inner, "summary_complete", None)
        reply = summarize(messages) if summarize else self.inner.complete(messages, [])
        self.budget.record(reply.usage)
        return reply

    def stream(self, messages, tools=()):
        self.budget.check()
        stream = iter(self.inner.stream(messages, tools))
        try:
            for event in stream:
                if event.type == "done" and event.reply is not None:
                    self.budget.record(event.reply.usage)
                yield event
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
