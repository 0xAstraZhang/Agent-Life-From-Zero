"""04–08 章正文项目的外部 HTTP 夹具，不进入读者手写目录。"""
import copy
import json

from reader_support import chat, tool


RESPONSES_FIRST = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "id": "reasoning_1", "summary": [], "encrypted_content": "fixture"},
        {"type": "function_call", "id": "item_1", "call_id": "call_1", "name": "add",
         "arguments": '{"a":2,"b":3}'},
    ],
}
RESPONSES_FINAL = {
    "status": "completed", "output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "结果是 5"}]},
    ],
}
MESSAGES_FIRST = {
    "role": "assistant", "stop_reason": "tool_use", "content": [
        {"type": "thinking", "thinking": "fixture", "signature": "fixture-signature"},
        {"type": "tool_use", "id": "call_1", "name": "add", "input": {"a": 2, "b": 3}},
    ],
}
MESSAGES_FINAL = {
    "role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "结果是 5"}],
}
MESSAGES_TWO = copy.deepcopy(MESSAGES_FIRST)
MESSAGES_TWO["content"].append({"type": "tool_use", "id": "call_2", "name": "add", "input": {"a": 2, "b": 3}})


def response_pair(requests, project, output):
    assert len(requests) == 2
    assert all(request["url"].endswith("/responses") for request in requests)
    first, second = (request["json"] for request in requests)
    assert first["tools"][0]["name"] == "add"
    assert RESPONSES_FIRST["output"][0] in second["input"]
    assert RESPONSES_FIRST["output"][1] in second["input"]
    assert second["input"][-1] == {"type": "function_call_output", "call_id": "call_1", "output": "5"}


def messages_pair(requests, project, output):
    assert len(requests) == 2
    assert all(request["url"].endswith("/messages") for request in requests)
    assert "x-api-key" in requests[0]["headers"]
    assert requests[0]["json"]["tools"][0]["input_schema"]["required"] == ["a", "b"]
    messages = requests[1]["json"]["messages"]
    assistant, results = messages[-2:]
    assert assistant["role"] == "assistant"
    assert assistant["content"][0]["signature"] == "fixture-signature"
    calls = [block for block in assistant["content"] if block["type"] == "tool_use"]
    assert results["role"] == "user" and len(results["content"]) == len(calls)
    assert [result["tool_use_id"] for result in results["content"]] == [call["id"] for call in calls]
    assert all(result["content"] == "5" for result in results["content"])
    assert all("_results" not in message for message in messages)


def chat_results(requests):
    history = requests[-1]["json"]["messages"]
    calls = {call["id"]: call for message in history for call in message.get("tool_calls", [])}
    results = [message for message in history if message["role"] == "tool"]
    assert results and all(result["tool_call_id"] in calls for result in results)
    return calls, results


def file_roundtrip(requests, project, output):
    calls, results = chat_results(requests)
    assert [calls[result["tool_call_id"]]["function"]["name"] for result in results] == ["Read", "Edit", "RunTests"]
    assert results[0]["content"] == "41\n"
    assert results[1]["content"] == "修改完成"
    assert results[2]["content"] == "检查通过"
    assert "最终文件： 42" in output


def approval_allowed(requests, project, output):
    calls, results = chat_results(requests)
    assert len(results) == 1
    assert calls[results[0]["tool_call_id"]]["function"]["name"] == "WriteNote"
    assert results[0]["content"] == "已创建 note.txt"
    assert "文件存在： True" in output


def approval_denied(requests, project, output):
    calls, results = chat_results(requests)
    assert [calls[result["tool_call_id"]]["function"]["name"] for result in results] == ["ReadNote", "WriteNote"]
    assert results[0]["content"] == "尚未创建"
    assert "拒绝" in results[1]["content"]
    assert output.count("允许创建练习文件？") == 1
    assert "文件存在： False" in output


def sse(*chunks):
    return "".join("data: " + (chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)) + "\n\n"
                   for chunk in chunks)


def chunk(delta, reason=None):
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}


TEXT_STREAM = sse(chunk({"content": "模型先提出工具调用，"}),
                  chunk({"content": "程序执行后把结果交回模型。"}), chunk({}, "stop"), "[DONE]")
CALL_STREAM = sse(
    chunk({"tool_calls": [{"index": 0, "id": "stream_call_1", "type": "function",
                            "function": {"name": "add", "arguments": '{"a":'}}]}),
    chunk({"tool_calls": [{"index": 0, "function": {"arguments": '2,"b":3}'}}]}),
    chunk({}, "tool_calls"), "[DONE]",
)
ANSWER_STREAM = sse(chunk({"content": "结果是 "}), chunk({"content": "5"}), chunk({}, "stop"), "[DONE]")


def stream_text_check(requests, project, output):
    assert len(requests) == 1 and requests[0]["json"]["stream"] is True
    assert not requests[0]["json"].get("tools")


def stream_tool_check(requests, project, output):
    assert len(requests) == 2 and all(request["json"]["stream"] is True for request in requests)
    calls, results = chat_results(requests)
    assert results[-1]["tool_call_id"] == "stream_call_1"
    assert results[-1]["content"] == "5"
    assert json.loads(calls["stream_call_1"]["function"]["arguments"]) == {"a": 2, "b": 3}


CASES = {
    (4, "minimal"): {"responses": [RESPONSES_FIRST, RESPONSES_FINAL],
                     "expected_output": ["本地工具结果： 5", "结果是 5"], "check": response_pair},
    (4, "enhanced"): {"responses": [RESPONSES_FIRST, RESPONSES_FINAL],
                      "expected_output": ["工具结果已回传： 5"], "check": response_pair},
    (5, "minimal"): {"responses": [MESSAGES_FIRST, MESSAGES_FINAL],
                     "expected_output": ["本地工具结果： 5"], "check": messages_pair},
    (5, "enhanced"): {"responses": [MESSAGES_TWO, MESSAGES_FINAL],
                      "expected_output": ["工具结果已回传： 5"], "check": messages_pair},
    (6, "minimal"): {"responses": [tool("Read", {}, "read_1"),
                                   tool("Edit", {"old": "41", "new": "42"}, "edit_1"),
                                   tool("RunTests", {}, "check_1"), chat("已修改并检查")],
                     "expected_output": ["最终文件： 42"], "check": file_roundtrip},
    (6, "enhanced"): {"responses": [tool("Read", {"path": "answer.txt"}, "read_1"),
                                    tool("Edit", {"path": "answer.txt", "old": "41", "new": "42"}, "edit_1"),
                                    tool("RunTests", {}, "check_1"), chat("已修改并检查")],
                      "expected_output": ["最终文件： 42"], "check": file_roundtrip},
    (7, "minimal"): {"responses": [tool("WriteNote", {"content": "hello"}), chat("已创建文件")],
                     "inputs": ["y"], "expected_output": ["文件存在： True"], "check": approval_allowed},
    (7, "enhanced"): {"responses": [tool("ReadNote", {}, "read_1"),
                                    tool("WriteNote", {"content": "hello"}, "write_1"), chat("用户拒绝，未创建文件")],
                      "inputs": ["n"], "expected_output": ["文件存在： False"], "check": approval_denied},
    (8, "text"): {"responses": [{"sse": TEXT_STREAM}],
                  "expected_output": ["模型先提出工具调用，程序执行后把结果交回模型。", "文本已完整接收"],
                  "check": stream_text_check},
    (8, "tools"): {"responses": [{"sse": CALL_STREAM}, {"sse": ANSWER_STREAM}],
                   "expected_output": ["工具结果已回传： 5"], "check": stream_tool_check},
}
