"""正文手写版的输入夹具；模型替身留在维护测试中，不放进读者项目。"""
from reader_support import chat, tool


def conversation(requests, project, output):
    history = requests[1]["json"]["messages"]
    assert [message["role"] for message in history] == ["user", "assistant", "user"]
    assert "小林" in history[0]["content"]
    assert history[1]["content"] == "记住了"


def tool_history(requests, project, output):
    history = requests[-1]["json"]["messages"]
    calls = {call["id"] for message in history for call in message.get("tool_calls", [])}
    results = [message for message in history if message["role"] == "tool"]
    assert results and all(result["tool_call_id"] in calls for result in results)
    assert results[-1]["content"] == "5"


def two_tools(requests, project, output):
    history = requests[-1]["json"]["messages"]
    calls = {call["id"] for message in history for call in message.get("tool_calls", [])}
    results = [message for message in history if message["role"] == "tool"]
    assert all(result["tool_call_id"] in calls for result in results)
    assert [result["content"] for result in results[-2:]] == ["5", "20"]


CASES = {
    (0, "minimal"): {"responses": [chat("你好")], "expected_output": ["你好"]},
    (0, "enhanced"): {
        "responses": [chat("记住了"), chat("你叫小林")],
        "expected_output": ["你叫小林"], "check": conversation,
    },
    (1, "minimal"): {
        "responses": [tool("add", {"a": 2, "b": 3}), chat("结果是 5")],
        "expected_output": ["工具结果： 5", "结果是 5"], "check": tool_history,
    },
    (1, "enhanced"): {
        "responses": [tool("add", {"a": "2", "b": 3}, "bad"),
                      tool("add", {"a": 2, "b": 3}), chat("结果是 5")],
        "expected_output": ["工具错误：", "工具结果： 5"], "check": tool_history,
    },
    (2, "minimal"): {
        "responses": [tool("add", {"a": 2, "b": 3}, "sum"),
                      tool("multiply", {"a": 5, "b": 4}, "product"), chat("结果是 20")],
        "expected_output": ["工具结果： 5", "工具结果： 20"], "check": two_tools,
    },
    (2, "enhanced"): {
        "responses": [tool("add", {"a": "2", "b": 3}, "bad"),
                      tool("add", {"a": 2, "b": 3}, "sum"),
                      tool("multiply", {"a": 5, "b": 4}, "product"), chat("结果是 20")],
        "expected_output": ["工具错误：", "工具结果： 20"], "check": two_tools,
    },
    (3, "minimal"): {
        "responses": [tool("add", {"a": 2, "b": 3}), chat("结果是 5")],
        "expected_output": ["结果是 5", "tool_call_id"], "check": tool_history,
    },
    (3, "enhanced"): {
        "responses": [tool("add", {"a": 2, "b": 3}), chat("结果是 5"), chat("刚才的结果是 5")],
        "expected_output": ["执行： add", "结果： 5", "刚才的结果是 5"], "check": tool_history,
    },
}
