"""09–13 章正文项目的外部 HTTP 夹具和实际请求验收。"""
import json

from reader_support import chat, tool


PROJECT_FACT = "项目代号是星河，测试命令是 python -m pytest。"
SKILL_BODY = "# 代码评审\n检查代码是否符合输入输出约定。\n\n步骤：先用具体输入推演，再对照预期结果。"
PLAN = [{"task": "定位问题", "status": "in_progress"}, {"task": "运行测试", "status": "pending"}]


def context_received(requests, project, output):
    message = requests[0]["json"]["messages"][0]
    assert message["role"] == "system"
    assert "测试命令是 python -m pytest" in message["content"]
    assert "项目约定：" in message["content"]


def restored_history(requests, project, output):
    messages = requests[1]["json"]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert "项目代号是星河" in messages[1]["content"]
    assert messages[2]["content"] == "记住了项目约定。"
    assert messages[3]["content"] == "项目代号和测试命令分别是什么？"


def compressed_view(requests, project, output):
    original_turns = json.loads(requests[1]["json"]["messages"][1]["content"])
    assert [m["role"] for m in original_turns] == ["user", "assistant"]
    assert "项目代号是星河" in original_turns[0]["content"]
    view = requests[2]["json"]["messages"]
    assert view[0]["role"] == "system"
    assert view[1]["content"].startswith("此前任务摘要：\n")
    assert PROJECT_FACT in view[1]["content"]
    assert view[-1]["content"] == "项目代号和测试命令分别是什么？"


def read_note_recorded(requests, project, output):
    result = requests[1]["json"]["messages"][-1]
    assert result["role"] == "tool" and result["tool_call_id"] == "read1"
    assert "python -m pytest" in result["content"]


def denied_write_keeps_hooks(requests, project, output):
    read_note_recorded(requests, project, output)
    result = requests[2]["json"]["messages"][-1]
    assert result["tool_call_id"] == "write1"
    assert "拒绝" in result["content"]
    assert "before:ReadNote" in output and "after:WriteNote" in output


def plan_updated(requests, project, output):
    result = requests[-1]["json"]["messages"][-1]
    assert json.loads(result["content"]) == PLAN


def invalid_plan_corrected(requests, project, output):
    assert "最多只能有一项" in requests[1]["json"]["messages"][-1]["content"]
    plan_updated(requests, project, output)


def independent_child(requests, project, output):
    parent_first = requests[0]["json"]["messages"]
    child_first = requests[1]["json"]["messages"]
    assert [m["role"] for m in child_first] == ["system", "user"]
    assert child_first[1]["content"] == "读取项目记录并报告测试命令。"
    assert parent_first[1]["content"] != child_first[1]["content"]
    assert {t["function"]["name"] for t in requests[1]["json"]["tools"]} == {"ReadNote"}
    child_second = requests[2]["json"]["messages"]
    assert child_second[-1]["role"] == "tool"
    assert "测试命令：python -m pytest" in child_second[-1]["content"]
    parent_second = requests[3]["json"]["messages"]
    assert parent_second[-1]["tool_call_id"] == "delegate1"
    assert parent_second[-1]["content"] == "project.txt 记载测试命令是 python -m pytest。"


def skill_is_loaded_on_demand(requests, project, output):
    initial = requests[0]["json"]["messages"][0]["content"]
    assert "review: 检查代码是否符合输入输出约定。" in initial
    assert "步骤：先用具体输入推演" not in initial
    result = requests[1]["json"]["messages"][-1]
    assert result["tool_call_id"] == "skill1" and result["content"] == SKILL_BODY


def session_responses():
    return [chat("记住了项目约定。"), chat(PROJECT_FACT)]


def compact_responses():
    return [chat("记住了项目约定。"), chat(PROJECT_FACT), chat(PROJECT_FACT)]


def delegate_responses():
    return [tool("Delegate", {"task": "读取项目记录并报告测试命令。"}, "delegate1"),
            tool("ReadNote", {}, "read1"), chat("project.txt 记载测试命令是 python -m pytest。"),
            chat("根据调查，运行 python -m pytest。")]


def skill_responses():
    return [tool("Skill", {"name": "review"}, "skill1"), chat("add(2, 3) 得到 -1，应把减法改为加法。")]


CASES = {
    (9, "minimal"): {
        "responses": [chat("项目约定：测试命令是 python -m pytest。")],
        "expected_output": ["本次 system：", "项目约定：测试命令是 python -m pytest。"],
        "check": context_received,
    },
    (9, "enhanced"): {
        "responses": [chat("项目约定：测试命令是 python -m pytest。")],
        "expected_output": ["缺失说明： ''", "越界说明已拒绝"], "check": context_received,
    },
    (10, "minimal"): {
        "responses": session_responses(), "expected_output": ["恢复历史： 3 条", PROJECT_FACT],
        "check": restored_history,
    },
    (10, "enhanced"): {
        "responses": session_responses(),
        "expected_output": ["续轮后已保存： 5 条", "损坏会话已拒绝"], "check": restored_history,
    },
    (10, "compact"): {
        "responses": compact_responses(),
        "expected_output": ["完整历史： 4 条；请求视图： 3 条", PROJECT_FACT], "check": compressed_view,
    },
    (10, "compact-enhanced"): {
        "responses": compact_responses(),
        "expected_output": ["摘要次数： 1", "完整历史仍保留： 5 条"], "check": compressed_view,
    },
    (11, "minimal"): {
        "responses": [tool("ReadNote", {}, "read1"), chat("使用 python -m pytest 运行测试。")],
        "expected_output": ["执行日志：", "ReadNote"], "check": read_note_recorded,
    },
    (11, "enhanced"): {
        "responses": [tool("ReadNote", {}, "read1"), tool("WriteNote", {}, "write1"), chat("读取成功，写入被拒绝。")],
        "expected_output": ["报告没有写入： True", "after:WriteNote"], "check": denied_write_keeps_hooks,
    },
    (11, "todo"): {
        "responses": [tool("TodoWrite", {"items": PLAN}, "plan1"), chat("计划已记录。")],
        "expected_output": ["实际保存的计划：", "定位问题", "运行测试"], "check": plan_updated,
    },
    (11, "todo-enhanced"): {
        "responses": [tool("TodoWrite", {"items": [{"task": "定位问题", "status": "in_progress"},
                                                      {"task": "运行测试", "status": "in_progress"}]}, "bad-plan"),
                      tool("TodoWrite", {"items": PLAN}, "plan1"), chat("计划已修正。")],
        "expected_output": ["实际保存的计划：", "计划已修正"], "check": invalid_plan_corrected,
    },
    (12, "minimal"): {
        "responses": delegate_responses(), "expected_output": ["子 Agent 实际读取：", "子 Agent 读取次数： 1"],
        "check": independent_child,
    },
    (12, "enhanced"): {
        "responses": delegate_responses(), "expected_output": ["子 Agent 读取次数： 1", "父子合计请求： 4"],
        "check": independent_child,
    },
    (13, "minimal"): {
        "responses": skill_responses(), "expected_output": ["技能正文已进入历史： True"],
        "check": skill_is_loaded_on_demand,
    },
    (13, "enhanced"): {
        "responses": skill_responses(),
        "expected_output": ["加入技能前：", "加入技能后：", "写入仍被拒绝，日志仍记录 Skill： True"],
        "check": skill_is_loaded_on_demand,
    },
}
