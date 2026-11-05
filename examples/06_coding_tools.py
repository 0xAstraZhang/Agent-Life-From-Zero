"""第 6 章：真实模型在临时练习目录中完成 Read → Edit → RunTests。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.coding_prototype import build_prototype_tools, check_tool
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.testing import call
from minicode.tool import ToolContext, ToolRunner


def main(argv=None):
    args = parse_args(__doc__, argv)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "answer.txt").write_text("41\n", encoding="utf-8")
        extra = {"path": "answer.txt"} if args.step == "enhanced" else {}
        script = [call("Read", **extra), call("Edit", old="41", new="42", **extra),
                  call("RunTests"), "已改成 42，检查通过。"]
        tools = build_prototype_tools()
        if args.step == "enhanced":
            tools = [tool for tool in build_coding_tools() if tool.spec.name in {"Read", "Edit"}]
            tools.append(check_tool())
        runner = ToolRunner(tools, ToolContext(root))
        with open_model(args, script) as model:
            agent = Agent(model, runner, max_turns=6)
            events = run_events(agent.run("先 Read answer.txt，再 Edit 将 41 改成 42，最后调用 RunTests 检查。"))
        names = {request.id: request.name for event in events for request in event.get("tool_calls", ())}
        successful = [names.get(event["call_id"]) for event in events
                      if event["type"] == "tool_result" and not event["is_error"]]
        if not all(name in successful for name in ("Read", "Edit", "RunTests")):
            raise RuntimeError("模型尚未实际完成 Read、Edit 和 RunTests")
        text = (root / "answer.txt").read_text(encoding="utf-8").strip()
        if text != "42":
            raise RuntimeError("最终文件没有变成 42")
        print("最终文件：", text)


if __name__ == "__main__":
    main()
