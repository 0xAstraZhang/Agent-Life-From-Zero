"""第 01 章：检查真实 wire 形状和工具结果回传，不依赖最终包。"""
import json

import httpx
import pytest

from lessons.ch00 import ChatModel, text_reply
from lessons.ch01 import run, tool_reply


def execute(replies, **kwargs):
    requests = []
    pending = iter(replies)

    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [next(pending)]})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        answer, messages = run(ChatModel(client, "fixture", "https://fixture.invalid"), "计算", **kwargs)
    return answer, messages, requests


def test_tool_roundtrip_keeps_call_and_matching_result():
    first = tool_reply("add", {"a": 7, "b": 8}, "addition")
    answer, messages, requests = execute([first, text_reply("15")])
    assert answer == "15"
    assert requests[1]["messages"] == [
        {"role": "user", "content": "计算"}, first["message"],
        {"role": "tool", "tool_call_id": "addition", "content": "15"}]
    assert messages[-1] == {"role": "assistant", "content": "15"}
    assert requests[0]["tools"][0]["function"]["name"] == "add"


def test_direct_answer_finishes_without_extra_request():
    answer, _, requests = execute([text_reply("你好")])
    assert answer == "你好" and len(requests) == 1


def test_enhanced_reports_bad_arguments_and_model_can_retry():
    _, _, requests = execute([
        tool_reply("add", {"a": "7", "b": 8}, "bad"),
        tool_reply("add", {"a": 7, "b": 8}, "good"), text_reply("15")], enhanced=True)
    assert requests[1]["messages"][-1]["tool_call_id"] == "bad"
    assert "必须是整数" in requests[1]["messages"][-1]["content"]
    assert requests[2]["messages"][-1] == {"role": "tool", "tool_call_id": "good", "content": "15"}


@pytest.mark.parametrize("enhanced", [False, True])
def test_truncated_call_never_executes(enhanced, monkeypatch):
    reply = tool_reply("add", {"a": 1, "b": 2})
    reply["finish_reason"] = "length"
    touched = []
    monkeypatch.setattr("lessons.ch01.add", lambda request: touched.append(request))
    monkeypatch.setattr("lessons.ch01.add_checked", lambda request: touched.append(request))
    with pytest.raises(RuntimeError, match="尚未正常完成"):
        execute([reply], enhanced=enhanced)
    assert not touched


def test_inconsistent_reply_and_repeated_id_are_explicit_errors():
    reply = tool_reply("add", {"a": 1, "b": 2})
    reply["finish_reason"] = "stop"
    with pytest.raises(ValueError, match="声明回答结束"):
        execute([reply], enhanced=True)
    call = tool_reply("add", {"a": 1, "b": 2}, "duplicate")
    with pytest.raises(ValueError, match="ID 重复"):
        execute([call, call], enhanced=True)


def test_model_turn_limit():
    with pytest.raises(RuntimeError, match="轮数上限"):
        execute([tool_reply("add", {"a": 1, "b": 2})], max_turns=1)


@pytest.mark.parametrize("step", ["minimal", "enhanced"])
def test_lesson_checkpoint_requires_actual_tool_use(step, monkeypatch):
    from lessons import ch00, ch01
    monkeypatch.setattr(ch01, "open_chat", lambda **kwargs: ch00.open_chat(offline=True, replies=[text_reply("5")]))
    with pytest.raises(RuntimeError, match="需要实际调用 add"):
        ch01.main(["--offline", "--step", step])
