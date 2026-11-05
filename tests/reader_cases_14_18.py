"""第 14–18 章手写项目的 HTTP 夹具；外部 echo 进程仍真实运行。"""
from reader_support import chat, tool


CASES = {
    (14, "minimal"): {
        "responses": [tool("echo", {"text": "你好，MCP"}), chat("外部工具返回了问候。")],
        "expected_output": ["echo 往返验收： True"],
    },
    (14, "enhanced"): {
        "responses": [tool("echo", {"text": "error: unavailable"}),
                      tool("echo", {"text": "你好，MCP"}, call_id="c2"),
                      chat("错误已经恢复，外部工具返回了问候。")],
        "expected_output": ["错误回传后恢复： True"],
    },
    (15, "minimal"): {
        "responses": [tool("ReadAnswer", {}), tool("SetAnswer", {"value": 42}, call_id="c2"), chat("已改成 42。")],
        "expected_output": ['"file_passed": true', '"actions_passed": true', '"passed": true'],
    },
    (15, "enhanced"): {
        "responses": [tool("ReadAnswer", {}), tool("SetAnswer", {"value": 42}, call_id="c2"), chat("已改成 42。"),
                      tool("ReadAnswer", {}, call_id="c3"), tool("SetAnswer", {"value": 100}, call_id="c4"), chat("已改成 100。")],
        "expected_output": ["任务通过：2/2"],
    },
    (16, "minimal"): {
        "responses": [tool("ListFiles", {}), chat("已列出目录。")],
        "expected_output": ["实际工具： ListFiles"],
    },
    (16, "enhanced"): {
        "responses": [tool("ListFiles", {}), chat("已列出目录。")],
        "expected_output": ['"tool_calls": ["ListFiles"]'],
    },
    (17, "minimal"): {
        "responses": [],
        "expected_output": ["沙箱检查点："],
    },
    (17, "enhanced"): {
        "responses": [],
        "expected_output": ["直接写入保护： True", "沙箱检查点："],
    },
    (18, "minimal"): {
        "responses": [chat("你好，运行记录。")],
        "expected_output": ["请求记录数： 1"],
    },
    (18, "enhanced"): {
        "responses": [{"raise": "ConnectError", "message": "fixture: connection not established"},
                      tool("Wait", {}), chat("等待完成。")],
        "expected_output": ["Wait 实际执行： True", "过期后阻止请求： True",
                            "连接重试次数： 1", "逻辑请求记录数： 2"],
    },
}
