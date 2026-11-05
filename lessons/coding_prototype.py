"""第 06 章最小工具：只修改练习目录中的一个文件。"""
from minicode.tool import Tool
from minicode.types import ToolResult, ToolSpec


EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}


def read_answer(args, context):
    return (context.cwd / "answer.txt").read_text(encoding="utf-8")


def edit_answer(args, context):
    path = context.cwd / "answer.txt"
    text = path.read_text(encoding="utf-8")
    if not args["old"] or text.count(args["old"]) != 1:
        raise ValueError("old 必须在文件中恰好出现一次")
    path.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
    return "已修改 answer.txt"


def check_answer(args, context):
    passed = read_answer({}, context).strip() == "42"
    return ToolResult("", "检查通过：answer.txt 是 42" if passed else "检查失败：answer.txt 应为 42",
                      is_error=not passed)


def check_tool():
    return Tool(ToolSpec("RunTests", "检查 answer.txt 的内容是否为 42", EMPTY),
                check_answer, read_only=True)


def build_prototype_tools():
    edit = ToolSpec("Edit", "在 answer.txt 中精确替换一处文本", {
        "type": "object", "properties": {"old": {"type": "string"}, "new": {"type": "string"}},
        "required": ["old", "new"], "additionalProperties": False,
    })
    return [Tool(ToolSpec("Read", "读取 answer.txt", EMPTY), read_answer, read_only=True),
            Tool(edit, edit_answer), check_tool()]
