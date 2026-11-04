from pathlib import Path

import pytest

from minicode.agent import Agent
from minicode.compact import CompactingModel
from minicode.context import build_system_prompt, load_project_instructions
from minicode.hooks import HookRunner
from minicode.permissions import PermissionRunner
from minicode.planning import TodoList, readonly_runner
from minicode.session import SessionStore, run_session
from minicode.skills import SkillLibrary
from minicode.subagents import BudgetModel, RequestBudget, subagent_tool
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, ModelReply, ToolCall, ToolResult, ToolSpec, Usage, message_text


def make_runner(tmp_path, outputs=None):
    outputs = outputs if outputs is not None else []
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    read = Tool(ToolSpec("Read", "read", schema), lambda args, ctx: "file content", True, True)
    write = Tool(ToolSpec("Write", "write", schema), lambda args, ctx: outputs.append("write") or "ok")
    return ToolRunner([read, write], ToolContext(tmp_path))


def test_project_instructions_are_explicit_and_bounded(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (tmp_path / "MINICODE.md").write_text("parent must not leak")
    assert load_project_instructions(root) == ""
    (root / "MINICODE.md").write_text("run pytest")
    assert "run pytest" in build_system_prompt(root, load_project_instructions(root))
    with pytest.raises(ValueError, match="工作区"):
        load_project_instructions(root, "../MINICODE.md")
    (root / "linked.md").symlink_to(tmp_path / "MINICODE.md")
    with pytest.raises(ValueError, match="工作区"):
        load_project_instructions(root, "linked.md")
    with pytest.raises(ValueError, match="超过"):
        load_project_instructions(root, max_chars=2)


def test_session_preserves_metadata_and_repairs_without_execution(tmp_path):
    first, second = call("Read"), call("Write")
    history = [Message.text("user", "check"), Message("assistant", [first, second], {"source": "demo"}),
               Message("tool", [ToolResult(first.id, "done")]), Message.text("user", "continue")]
    store = SessionStore(tmp_path / "s.jsonl", source={"api": "demo", "api_key": "secret"})
    store.save(history)
    restored = store.load()
    assert restored[1].provider_data == {"source": "demo"}
    assert restored[3].content[0].call_id == second.id
    assert restored[3].content[0].is_error
    assert restored[4].role == "user"
    assert len(history) == 4
    assert "secret" not in store.path.read_text()
    assert store.load(repair=False) == history


def test_session_save_after_generator_close_repairs(tmp_path):
    model = FakeModel([call("Write"), "done"])
    touched = []
    agent = Agent(model, make_runner(tmp_path, touched))
    store = SessionStore(tmp_path / "s.jsonl")
    events = run_session(agent, "write", store)
    assert next(events)["type"] == "assistant"
    events.close()
    history = store.load(repair=False)
    assert history[-1].content[0].is_error
    assert not touched


def test_session_rejects_corrupt_version(tmp_path):
    store = SessionStore(tmp_path / "s.jsonl")
    store.path.write_text('{"type":"session","version":999}\n')
    with pytest.raises(ValueError, match="版本"):
        store.load()


@pytest.mark.parametrize("history", [
    [Message("tool", [ToolResult("missing", "result")])],
    [Message("assistant", [ToolCall("a", "Read", {})]),
     Message("tool", [ToolResult("other", "result")])],
    [Message("assistant", [ToolCall("a", "Read", {})]),
     Message("tool", [ToolResult("a", "first"), ToolResult("a", "second")])],
])
def test_session_rejects_broken_pairing(tmp_path, history):
    store = SessionStore(tmp_path / "s.jsonl")
    store.save(history)
    with pytest.raises(ValueError):
        store.load()


def transcript():
    request = call("Read")
    return [Message.text("system", "system"), Message.text("user", "old goal"),
            Message("assistant", [request]), Message("tool", [ToolResult(request.id, "x" * 300)]),
            Message.text("assistant", "old answer"), Message.text("user", "NEW goal, keep exact")]


def test_compaction_keeps_turns_and_full_history_and_counts_summary():
    history = transcript()
    summary = ModelReply(Message.text("assistant", "old summary"), Usage(8, 3), "stop")
    final = ModelReply(Message.text("assistant", "answer"), Usage(5, 2), "stop")
    backend = FakeModel([summary, final, "again"])
    model = CompactingModel(backend, threshold_chars=1, keep_turns=1)
    result = model.complete(history, [])
    assert result.usage == Usage(13, 5)
    sent = backend.requests[1]["messages"]
    assert message_text(sent[-1]) == "NEW goal, keep exact"
    assert all(m.role != "tool" for m in sent)
    assert "old summary" in message_text(sent[1])
    assert len(history[3].content[0].content) == 300
    model.complete(history, [])
    assert model.compactions == 1
    assert len(backend.requests) == 3  # 同一前缀的摘要重用，不再请求模型。


def test_compaction_failure_preserves_original_request():
    history = transcript()
    backend = FakeModel([ModelReply(Message.text("assistant", "partial"), Usage(), "length"), "ok"])
    model = CompactingModel(backend, threshold_chars=1, keep_turns=1)
    model.complete(history)
    assert backend.requests[1]["messages"] == history
    assert model.compactions == 0
    assert model.last_error


def test_compaction_stream_has_final_reply():
    backend = FakeModel(["summary", "result"])
    model = CompactingModel(backend, threshold_chars=1, keep_turns=1)
    events = list(model.stream(transcript()))
    assert events[-1].type == "done"
    assert message_text(events[-1].reply.message) == "result"


def test_compaction_stream_closes_underlying_iterator():
    closed = []

    class Model:
        def stream(self, messages, tools):
            from minicode.types import StreamEvent
            try:
                yield StreamEvent("text_delta", text="partial")
                yield StreamEvent("text_delta", text="never")
            finally:
                closed.append(True)

    events = CompactingModel(Model()).stream([Message.text("user", "question")])
    next(events)
    events.close()
    assert closed == [True]


def test_hook_rewrite_still_passes_permissions(tmp_path):
    touched = []
    runner = PermissionRunner(make_runner(tmp_path, touched))
    hooks = HookRunner(runner, before=lambda c: ToolCall(c.id, "Write", {}))
    result = hooks.run(call("Read"))
    assert result.is_error
    assert touched == []


def test_hooks_cannot_change_call_id_or_mask_failure(tmp_path):
    runner = make_runner(tmp_path)
    invalid = HookRunner(runner, before=lambda c: ToolCall("other", "Read", {}))
    assert invalid.run(call("Read")).is_error
    observe = HookRunner(runner, after=lambda c, r: setattr(r, "is_error", False))
    assert observe.run(call("Missing")).is_error


def test_todo_requires_one_active_item_and_plan_blocks_writes(tmp_path):
    todo = TodoList()
    runner = ToolRunner([todo.tool()], ToolContext(tmp_path))
    result = runner.run(call("TodoWrite", items=[{"task": "a", "status": "in_progress"},
                                                {"task": "b", "status": "in_progress"}]))
    assert result.is_error and todo.items == []
    result = runner.run(call("TodoWrite", items=[{"task": "a", "status": "done"}]))
    assert not result.is_error and todo.items[0]["status"] == "done"
    read_only = readonly_runner(make_runner(tmp_path))
    assert [spec.name for spec in read_only.specs()] == ["Read"]
    assert read_only.run(call("Write")).is_error


def test_subagent_has_own_context_readonly_tools_and_no_nested_delegate(tmp_path):
    touched = []
    base = make_runner(tmp_path, touched)
    child_model = FakeModel([call("Write"), call("Read"), "evidence summary"])
    delegate = subagent_tool(lambda: child_model, lambda ctx: parent)
    parent = ToolRunner([*base.tools.values(), delegate], ToolContext(tmp_path))
    result = parent.run(call("Delegate", task="investigate"))
    assert result.content == "evidence summary" and not result.is_error
    assert touched == []
    assert [tool.name for tool in child_model.requests[0]["tools"]] == ["Read"]
    assert [m.role for m in child_model.requests[0]["messages"]] == ["system", "user"]


def test_shared_request_budget_counts_parent_and_child():
    budget = RequestBudget(2)
    parent = BudgetModel(FakeModel(["parent"]), budget)
    child = BudgetModel(FakeModel(["child", "never"]), budget)
    parent.complete([])
    child.complete([])
    with pytest.raises(RuntimeError, match="预算"):
        list(child.stream([]))
    assert budget.used == 2


def test_skills_disclose_on_demand_and_reject_escape(tmp_path):
    directory = tmp_path / "skills"
    skill = directory / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Review\nRead code carefully.\n\nDetailed body.")
    library = SkillLibrary(directory)
    assert "review: Read code carefully." in library.catalog()
    assert "Detailed body" not in library.catalog()
    assert "Detailed body" in library.load("review")
    with pytest.raises(ValueError):
        library.load("../outside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("private")
    (directory / "linked").symlink_to(outside)
    assert "linked" not in library.catalog()
    with pytest.raises(ValueError):
        library.load("linked")


def test_skill_oversize_returns_tool_error(tmp_path):
    folder = tmp_path / "big"
    folder.mkdir()
    (folder / "SKILL.md").write_text("x" * 100)
    library = SkillLibrary(tmp_path, max_chars=20)
    runner = ToolRunner([library.tool()], ToolContext(tmp_path))
    result = runner.run(call("Skill", name="big"))
    assert result.is_error
