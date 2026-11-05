"""真实阻塞、并行与协议边界；不调用外部模型。"""
import signal
import threading
import time

import pytest

from minicode.agent import Agent
from minicode.budget import Budget, BudgetModel
from minicode.compact import CompactingModel
from minicode.hooks import HookRunner
from minicode.permissions import PermissionRunner
from minicode.subagents import subagent_tool
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, ModelReply, ToolCall, ToolSpec, Usage


SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


def runner(tmp_path, tools=()):
    return ToolRunner(list(tools), ToolContext(tmp_path))


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="CLI wall-clock interruption uses Unix timers")
def test_deadline_interrupts_blocked_model_and_restores_signal_handler(tmp_path):
    closed = []

    class Slow:
        deadline = None

        def set_deadline(self, value):
            self.deadline = value

        def complete(self, messages, tools):
            try:
                time.sleep(2)
            finally:
                closed.append(True)
            return ModelReply(Message.text("assistant", "too late"), Usage(), "stop")

    original_handler = signal.getsignal(signal.SIGALRM)
    model = Slow()
    wrapped = CompactingModel(BudgetModel(model, Budget()))
    agent = Agent(wrapped, runner(tmp_path), task_timeout=0.05)
    started = time.monotonic()
    result = list(agent.run("work"))[-1]
    assert result["status"] == "timeout"
    assert time.monotonic() - started < 0.6
    assert closed == [True]
    assert model.deadline is None
    assert signal.getsignal(signal.SIGALRM) == original_handler
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


@pytest.mark.skipif(not hasattr(signal, "setitimer"), reason="Unix timer")
def test_deadline_cleans_up_blocked_tool_and_repairs_history(tmp_path):
    closed = []

    def slow(args, ctx):
        try:
            time.sleep(2)
        finally:
            closed.append(True)

    tool = Tool(ToolSpec("Slow", "slow", SCHEMA), slow)
    agent = Agent(FakeModel([call("Slow"), "never"]), runner(tmp_path, [tool]), task_timeout=0.05)
    events = list(agent.run("work"))
    assert events[-1]["status"] == "timeout"
    assert closed == [True]
    assert agent.history[-1].role == "tool"
    assert agent.history[-1].content[0].is_error


def test_no_timer_is_left_running_while_consumer_owns_control(tmp_path):
    tool = Tool(ToolSpec("Read", "read", SCHEMA), lambda args, ctx: "ok", True, True)
    events = Agent(FakeModel([call("Read")]), runner(tmp_path, [tool]), task_timeout=0.01).run("read")
    assert next(events)["type"] == "assistant"
    time.sleep(0.02)
    assert list(events)[-1]["status"] == "timeout"


def test_read_calls_overlap_but_results_follow_declared_order(tmp_path):
    barrier = threading.Barrier(2, timeout=1)
    observations = []

    def read(args, ctx):
        barrier.wait()
        if args["n"] == 1:
            time.sleep(0.02)
        return str(args["n"])

    schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
    tool = Tool(ToolSpec("Read", "read", schema), read, True, True)
    calls = [call("Read", n=1), call("Read", n=2)]
    agent = Agent(FakeModel([calls, "done"]), runner(tmp_path, [tool]), parallel_tools=2,
                  observer=observations.append)
    events = list(agent.run("read both"))
    assert events[-1]["status"] == "success"
    assert [event["content"] for event in events if event["type"] == "tool_result"] == ["1", "2"]
    assert len([event for event in observations if event["type"] == "tool_start"]) == 2
    assert all("agent_id" in event for event in observations)


def test_writes_are_a_barrier_and_permissions_still_apply(tmp_path):
    order = []
    read = Tool(ToolSpec("Read", "read", SCHEMA), lambda args, ctx: order.append("read") or "ok", True, True)
    write = Tool(ToolSpec("Write", "write", SCHEMA), lambda args, ctx: order.append("write") or "ok")
    protected = PermissionRunner(runner(tmp_path, [read, write]))
    model = FakeModel([[call("Read"), call("Read"), call("Write"), call("Read")], "done"])
    events = list(Agent(model, protected, parallel_tools=4).run("work"))
    results = [event for event in events if event["type"] == "tool_result"]
    assert order == ["read", "read", "read"]
    assert results[2]["is_error"]


def test_rewriting_hooks_disable_read_parallelism(tmp_path):
    threads = []
    read = Tool(ToolSpec("Read", "read", SCHEMA), lambda args, ctx: "ok", True, True)
    write = Tool(ToolSpec("Write", "write", SCHEMA), lambda args, ctx: threads.append(threading.current_thread()) or "ok")
    wrapped = HookRunner(runner(tmp_path, [read, write]),
                         before=lambda request: ToolCall(request.id, "Write", {}))
    model = FakeModel([[call("Read"), call("Read")], "done"])
    list(Agent(model, wrapped, parallel_tools=2).run("work"))
    assert threads == [threading.main_thread(), threading.main_thread()]


def test_child_events_are_identified_and_inherit_parent_deadline(tmp_path):
    observed, deadlines = [], []

    class Inspect(FakeModel):
        deadline = None

        def set_deadline(self, value):
            self.deadline = value

        def complete(self, messages, tools=()):
            deadlines.append(self.deadline)
            return super().complete(messages, tools)

    child_model = Inspect(["child summary"])
    delegate = subagent_tool(lambda: child_model, lambda ctx: runner(tmp_path), observer=observed.append)
    parent = Agent(FakeModel([call("Delegate", task="inspect"), "done"]), runner(tmp_path, [delegate]),
                   task_timeout=2, observer=observed.append)
    list(parent.run("work"))
    starts = [event for event in observed if event["type"] == "agent_start"]
    assert len(starts) == 2
    assert starts[1]["agent_id"].startswith("child-") and starts[1]["parent_id"] == "main"
    assert deadlines[0] == starts[0]["deadline"]
    assert child_model.deadline is None


@pytest.mark.parametrize("setting", [
    {"task_timeout": float("nan")}, {"task_timeout": float("inf")},
    {"deadline": float("inf")}, {"parallel_tools": 1.5}, {"parallel_tools": True},
])
def test_invalid_deadline_and_worker_settings_are_rejected(tmp_path, setting):
    with pytest.raises(ValueError):
        Agent(FakeModel([]), runner(tmp_path), **setting)


def test_cancelled_parallel_batch_keeps_own_deadline_and_cannot_log_next_query(tmp_path):
    exited = [threading.Event(), threading.Event()]
    contexts, observed = [], []
    lock = threading.Lock()

    def read(args, ctx):
        with lock:
            index = len(contexts)
            contexts.append(ctx)
        try:
            while True:
                ctx.check_deadline()
                time.sleep(0.06)
        finally:
            exited[index].set()

    tool = Tool(ToolSpec("Read", "read", SCHEMA), read, True, True)
    agent = Agent(FakeModel([[call("Read"), call("Read")], "next query succeeds"]),
                  runner(tmp_path, [tool]), task_timeout=0.02, parallel_tools=2, observer=observed.append)
    assert list(agent.run("first"))[-1]["status"] == "timeout"
    boundary = len(observed)
    agent.task_timeout = 1
    assert list(agent.run("second"))[-1]["status"] == "success"
    for event in exited:
        assert event.wait(0.3)
    assert all(context.deadline is not None for context in contexts)
    assert contexts[0] is not agent.runner.context
    assert all(event["type"] not in {"tool_start", "tool_end"} for event in observed[boundary:])
    assert agent.runner.context.deadline is None
