"""第 1 章独立验收，不导入未来章节。"""
from pathlib import Path
import subprocess
import sys

from lessons import ch01

stage = ch01

def execute(stage, model, root, max_turns=3):
    return list(stage.run(model, "读取记录", root, max_turns))


def test_stage_tool_roundtrip(tmp_path):
    (tmp_path / "record.txt").write_text("17", encoding="utf-8")
    model = ch01.FakeModel([ch01.call("read_file", path="record.txt"), "17"])
    events = execute(stage, model, tmp_path)
    assert events[-1]["status"] == "success" and events[-1]["turns"] == 2
    result = model.requests[1]["messages"][-1].content[0]
    assert result.content == "17" and not result.is_error
    assert result.call_id == model.requests[1]["messages"][-2].content[0].id



def test_stage_missing_tool_returns_error_to_model(tmp_path):
    model = ch01.FakeModel([ch01.call("missing"), "工具不存在"])
    events = execute(stage, model, tmp_path)
    assert events[0]["is_error"]
    assert model.requests[1]["messages"][-1].content[0].is_error
    assert events[-1]["status"] == "success"



def test_stage_length_does_not_call_tools(tmp_path):
    reply = ch01.ModelReply(ch01.Message("assistant", [ch01.call("read_file", path="record.txt")]), "length")
    events = execute(stage, ch01.FakeModel([reply]), tmp_path)
    assert [e["type"] for e in events] == ["result"]
    assert events[-1]["status"] == "length"



def test_stage_error_and_turn_limit(tmp_path):
    assert execute(stage, ch01.FakeModel([ConnectionError("offline")]), tmp_path)[-1]["status"] == "error"
    events = execute(stage, ch01.FakeModel([ch01.call("missing")]), tmp_path, max_turns=1)
    assert events[-1]["status"] == "max_turns"
    assert events[0]["is_error"]



def test_first_stage_is_standalone_without_final_package(tmp_path):
    source = Path(ch01.__file__).read_text()
    script = tmp_path / "agent.py"
    script.write_text(source)
    result = subprocess.run([sys.executable, "-I", str(script)], cwd=tmp_path,
                            capture_output=True, text=True, check=True)
    assert "success" in result.stdout and "1.37" in result.stdout
