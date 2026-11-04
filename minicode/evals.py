"""离线验收：检查任务造成的文件变化，而不只检查模型说了什么。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from .agent import Agent
from .coding_tools import build_coding_tools
from .permissions import PermissionRunner
from .testing import FakeModel, call
from .tool import ToolContext, ToolRunner


def run_evals() -> list[dict]:
    reports = []
    with TemporaryDirectory(prefix="minicode-eval-") as directory:
        cwd = Path(directory)
        target = cwd / "answer.py"
        target.write_text("answer = 41\n", encoding="utf-8")
        scenarios = [
            ("read_then_edit", [call("Read", path="answer.py"),
                                call("Edit", path="answer.py", old="41", new="42"), "完成"],
             ("Edit",), lambda: target.read_text() == "answer = 42\n"),
            ("deny_write", [call("Write", path="denied.txt", content="x"), "已停止"],
             (), lambda: not (cwd / "denied.txt").exists()),
            ("unknown_tool", [call("Missing"), "已说明错误"],
             (), lambda: True),
        ]
        for name, script, allowed, check in scenarios:
            model = FakeModel(script)
            runner = PermissionRunner(ToolRunner(build_coding_tools(include_bash=False), ToolContext(cwd)),
                                      allowed=allowed)
            events = list(Agent(model, runner).run(name))
            results = [e for e in events if e["type"] == "tool_result"]
            tool_errors = sum(e["is_error"] for e in results)
            expected_errors = 0 if name == "read_then_edit" else 1
            passed = (events[-1]["status"] == "success" and check()
                      and tool_errors == expected_errors)
            reports.append({"task": name, "passed": passed, "tool_errors": tool_errors})
    return reports


def main():
    import json
    reports = run_evals()
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(r["passed"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
