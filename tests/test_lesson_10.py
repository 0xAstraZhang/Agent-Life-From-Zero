"""第 10 章：落盘保留工具往返；压缩不改变原始历史。"""
from copy import deepcopy

from lessons.ch10 import compact_history, load_history, save_history
from minicode.testing import FakeModel
from minicode.types import Message, ToolCall, ToolResult, message_text


def history_with_tool():
    return [Message.text("system", "项目助手"), Message.text("user", "读取记录"),
            Message("assistant", [ToolCall("c1", "Read", {"path": "note.txt"})], {"opaque": [1, 2]}),
            Message("tool", [ToolResult("c1", "测试命令是 pytest")]),
            Message.text("assistant", "已经找到测试命令。")]


def test_save_load_keeps_call_pair_and_provider_data(tmp_path):
    original = history_with_tool()
    path = tmp_path / "history.json"
    save_history(path, original)
    assert load_history(path) == original


def test_compact_keeps_latest_request_and_entire_old_turn():
    history = history_with_tool() + [Message.text("user", "接下来怎么做？")]
    original = deepcopy(history)
    model = FakeModel(["此前已读取记录，测试命令是 pytest。"])
    request = compact_history(model, history)
    assert history == original
    assert request[0] == history[0] and request[-1] == history[-1]
    transcript = message_text(model.requests[0]["messages"][1])
    assert '"id": "c1"' in transcript and '"call_id": "c1"' in transcript
    assert all(message.role != "tool" for message in request)


def test_checkpoints(capsys):
    import runpy
    from pathlib import Path
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/10_sessions.py"))["main"]
    for step in ("minimal", "enhanced", "compact", "compact-enhanced"):
        main(["--offline", "--step", step])
        output = capsys.readouterr().out
        assert "星河项目使用 python -m pytest" in output
        assert "error " not in output


def test_checkpoint_rejects_lost_history(monkeypatch):
    from pathlib import Path
    import runpy
    import pytest
    main = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/10_sessions.py"))["main"]
    monkeypatch.setitem(main.__globals__, "load_history", lambda path: [])
    with pytest.raises(RuntimeError, match="恢复的历史与保存内容不同"):
        main(["--offline", "--step", "minimal"])
