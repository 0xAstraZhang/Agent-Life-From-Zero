"""运行：uv run python examples/10_sessions.py"""
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.agent import Agent
from minicode.compact import CompactingModel
from minicode.session import SessionStore, run_session
from minicode.testing import FakeModel
from minicode.tool import ToolContext, ToolRunner

with TemporaryDirectory() as directory:
    root = Path(directory)
    store = SessionStore(root / "session.jsonl", source={"api": "fake", "model": "script"})
    runner = ToolRunner([], ToolContext(root))
    first = Agent(FakeModel(["已确认测试入口。"]), runner)
    list(run_session(first, "先找到测试入口", store))
    backend = FakeModel(["此前用户要求找到测试入口，现已完成。", "下一步运行测试。"])
    model = CompactingModel(backend, threshold_chars=1, keep_turns=1)
    resumed = Agent(model, runner, history=store.load())
    final = list(run_session(resumed, "继续下一步", store))[-1]
    assert len(store.load()) == 5
    assert model.compactions == 1
    print(final["text"])
    print(f"完整历史 {len(store.load())} 条，模型请求 {len(backend.requests[-1]['messages'])} 条")
