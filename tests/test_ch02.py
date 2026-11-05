"""第 02 章：工具注册表改变动作集合，不改变循环算法。"""
import json

import httpx
import pytest

from lessons.ch00 import ChatModel, text_reply
from lessons.ch01 import tool_reply
from lessons.ch02 import TOOLS, execute_checked, run


def test_registered_tools_chain_preserves_both_results():
    requests = []
    replies = iter([tool_reply("add", {"a": 2, "b": 3}, "sum"),
                    tool_reply("multiply", {"a": 5, "b": 4}, "product"), text_reply("20")])

    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [next(replies)]})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        answer, _ = run(ChatModel(client, "fixture", "https://fixture.invalid"), "计算")
    assert answer == "20"
    assert {item["function"]["name"] for item in requests[0]["tools"]} == {"add", "multiply"}
    assert [item for item in requests[2]["messages"] if item["role"] == "tool"] == [
        {"role": "tool", "tool_call_id": "sum", "content": "5"},
        {"role": "tool", "tool_call_id": "product", "content": "20"}]


def test_validation_happens_before_callback():
    touched = []
    tools = {"add": {**TOOLS["add"], "execute": lambda **args: touched.append(args)}}
    request = tool_reply("add", {"a": "2", "b": 3})["message"]["tool_calls"][0]
    assert "工具错误" in execute_checked(request, tools)
    assert not touched


@pytest.mark.parametrize("name,args", [("missing", {}), ("add", {"a": 2}), ("add", {"a": 2, "b": 3, "c": 4})])
def test_invalid_calls_return_errors(name, args):
    request = tool_reply(name, args)["message"]["tool_calls"][0]
    assert execute_checked(request, TOOLS).startswith("工具错误：")


def test_adding_a_tool_does_not_change_loop():
    tools = {"echo": {"spec": {"type": "function", "function": {
        "name": "echo", "description": "回声", "parameters": {"type": "object"}}},
        "execute": lambda value: value}}
    replies = iter([tool_reply("echo", {"value": "new-tool"}), text_reply("完成")])
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [next(replies)]})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        run(ChatModel(client, "fixture", "https://fixture.invalid"), "回声", tools=tools, enhanced=True)
    assert requests[1]["messages"][-1]["content"] == "new-tool"


@pytest.mark.parametrize("step", ["minimal", "enhanced"])
def test_lesson_checkpoint_requires_both_tools(step, monkeypatch):
    from lessons import ch00, ch02
    replies = [tool_reply("multiply", {"a": 5, "b": 4}), text_reply("20")]
    monkeypatch.setattr(ch02, "open_chat", lambda **kwargs: ch00.open_chat(offline=True, replies=replies))
    with pytest.raises(RuntimeError, match="需要实际调用 add"):
        ch02.main(["--offline", "--step", step])
