"""正文中的错误处理与验收必须在独立项目里真实生效。"""
import json

import pytest

from scripts.reader_project import export_reader, verify_reader


@pytest.mark.parametrize("step", ["minimal", "enhanced"])
def test_missing_skill_returns_to_the_tool_caller(tmp_path, step):
    project = export_reader(13, tmp_path / "reader", step)
    (project / "demo.py").write_text('''from pathlib import Path
from skills import SkillLibrary
from tools import execute_call

library = SkillLibrary(Path("missing-skills"))
request = {"id": "missing", "function": {"name": "Skill", "arguments": '{"name":"absent"}'}}
result = execute_call(request, {"Skill": library.tool()})
assert result.startswith("工具错误："), result
print("读取失败已作为工具结果返回")
''', encoding="utf-8")
    verify_reader(13, step, project, {"responses": [], "expected_output": ["读取失败已作为工具结果返回"]})


def test_hook_checkpoint_cannot_pass_without_the_promised_read(tmp_path):
    project = export_reader(11, tmp_path / "reader", "enhanced")
    call = {"id": "write1", "type": "function", "function": {
        "name": "WriteNote", "arguments": json.dumps({}),
    }}
    responses = [
        {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [call]},
                       "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"role": "assistant", "content": "读取完成，写入被拒绝。"},
                       "finish_reason": "stop"}]},
    ]
    verify_reader(11, "enhanced", project, {
        "responses": responses, "expected_exit": 1,
        "expected_output": ["没有完整观察到读取和写入拒绝"],
    })
