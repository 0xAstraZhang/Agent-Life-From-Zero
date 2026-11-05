"""第 7 章：模型提出写入，用户批准或拒绝，结果再交回模型。"""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.runtime import open_model, run_events
from minicode.agent import Agent
from minicode.permissions import Approval, PermissionRunner
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolResult, ToolSpec


class ConfirmRunner:
    """第一步只增加一个确认点，后续再使用完整 PermissionRunner。"""
    def __init__(self, inner, ask):
        self.inner, self.ask = inner, ask
        self.context, self.tools = inner.context, inner.tools

    def specs(self):
        return self.inner.specs()

    def run(self, request):
        if self.ask(request.name, request.arguments):
            return self.inner.run(request)
        return ToolResult(request.id, "用户拒绝写入；请说明未执行，不要绕过拒绝。", True)


def write_note(args, context):
    (context.cwd / "note.txt").write_text(args["content"], encoding="utf-8")
    return "已写入 note.txt"


def make_runner(root):
    spec = ToolSpec("WriteNote", "在练习目录创建 note.txt", {
        "type": "object", "properties": {"content": {"type": "string"}},
        "required": ["content"], "additionalProperties": False,
    })
    return ToolRunner([Tool(spec, write_note)], ToolContext(root))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--api", default="openai-chat")
    parser.add_argument("--step", choices=("minimal", "enhanced"), default="minimal")
    parser.add_argument("--decision", choices=("ask", "allow", "deny"), default="ask")
    args = parser.parse_args(argv)
    if args.offline and args.decision == "ask":
        # 自动回归无需终端交互；拒绝不会因为没有输入而变成允许。
        args.decision = "deny"

    decisions = []

    def ask(name, arguments):
        print("模型请求：", name, arguments)
        if args.decision == "ask":
            allowed = input("允许写入练习文件？[y/N] ").strip().lower() == "y"
        else:
            print("本次决定：", args.decision)
            allowed = args.decision == "allow"
        decisions.append(allowed)
        return allowed

    with TemporaryDirectory() as directory:
        root = Path(directory)
        inner = make_runner(root)
        runner = ConfirmRunner(inner, ask)
        if args.step == "enhanced":
            runner = PermissionRunner(inner, Approval(ask))
        reply = "已创建 note.txt。" if args.decision == "allow" else "写入被拒绝，未创建文件。"
        with open_model(args, [call("WriteNote", content="hello"), reply]) as model:
            agent = Agent(model, runner, max_turns=3)
            events = run_events(agent.run(
                "请调用 WriteNote 写入 hello，再根据工具结果说明是否已创建文件。如果拒绝，不要重试。"
            ))
        exists = (root / "note.txt").exists()
        results = [event for event in events if event["type"] == "tool_result"]
        if not decisions or len(results) != len(decisions):
            raise RuntimeError("尚未完成模型调用、审批与工具结果的往返")
        if exists != any(decisions):
            raise RuntimeError("文件是否创建与审批决定不一致")
        if any(event["is_error"] == allowed for event, allowed in zip(results, decisions)):
            raise RuntimeError("工具结果未正确反映审批决定")
        if exists and (root / "note.txt").read_text(encoding="utf-8") != "hello":
            raise RuntimeError("文件内容不是要求的 hello")
        print("文件存在：", exists)


if __name__ == "__main__":
    main()
