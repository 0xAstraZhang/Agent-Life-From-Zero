"""第 15 章验收，不导入第 16 章 CLI。"""
import pytest

from minicode.budget import Budget, BudgetModel
from minicode.testing import FakeModel
from minicode.types import Message, ModelReply, Usage


def test_budget_blocks_next_request_and_stream_counts_once():
    reply = ModelReply(Message.text("assistant", "done"), Usage(8, 2), "stop")
    fake = FakeModel([reply, "must not run"])
    budget = Budget(10)
    model = BudgetModel(fake, budget)
    events = list(model.stream([Message.text("user", "hello")]))
    assert events[-1].type == "done"
    assert budget.usage == Usage(8, 2)
    with pytest.raises(RuntimeError, match="预算"):
        model.complete([Message.text("user", "again")])
    assert len(fake.requests) == 1

def test_budget_stream_close_releases_inner_generator():
    from minicode.types import StreamEvent
    closed = []
    class Model:
        def stream(self, messages, tools=()):
            try:
                yield StreamEvent("text_delta", text="partial")
            finally:
                closed.append(True)
    stream = BudgetModel(Model(), Budget()).stream([])
    next(stream)
    stream.close()
    assert closed == [True]
