"""第 16 章：一个 prompt 的最小入口，再使用完整 CLI。"""
import argparse
import os
from pathlib import Path

from lessons.runtime import open_model, print_event
from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.permissions import PermissionRunner
from minicode.testing import call
from minicode.tool import ToolContext, ToolRunner

DEFAULT_PROMPT = "使用 Glob 列出工作目录中的 Python 文件，再简要说明。"


def parser():
    p = argparse.ArgumentParser(description="从最小 main 组装 CLI")
    p.add_argument("-p", "--prompt", default=DEFAULT_PROMPT)
    p.add_argument("--cwd", type=Path, default=Path.cwd())
    p.add_argument("--api", default=os.getenv("MINICODE_API", "openai-chat"),
                   choices=("openai-chat", "openai-responses", "anthropic-messages"))
    p.add_argument("--offline", action="store_true", help="显式使用离线剧本")
    p.add_argument("--step", choices=("minimal", "enhanced"), default="minimal")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.step == "enhanced":
        from minicode.cli import main as full_main
        options = ["--api", args.api, "--cwd", str(args.cwd), "--read-only", "-p", args.prompt]
        if args.offline:
            options.append("--demo")
        else:
            from examples.live_model import config_from_env, parser as model_parser
            config = config_from_env(model_parser().parse_args(["--api", args.api]))
            prefix = "MINICODE" if any(os.getenv(f"MINICODE_{key}")
                                       for key in ("MODEL", "BASE_URL", "API_KEY")) else "LLM"
            options += ["--model", config.model, "--base-url", config.base_url,
                        "--provider", config.provider, "--api-key-env", f"{prefix}_API_KEY"]
            if config.chat_token_field:
                options += ["--chat-token-field", config.chat_token_field]
        return full_main(options)
    root = args.cwd.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("工作目录不存在")
    script = [call("Glob", pattern="*.py"), "已列出 Python 文件。"]
    with open_model(args, script) as model:
        runner = PermissionRunner(
            ToolRunner(build_coding_tools(include_bash=False), ToolContext(root)), read_only=True)
        final, glob_ok, glob_ids = None, False, set()
        for event in Agent(model, runner, max_turns=6).run(args.prompt):
            print_event(event)
            if event["type"] == "assistant":
                glob_ids.update(c.id for c in event.get("tool_calls", []) if c.name == "Glob")
            if event["type"] == "tool_result" and event["call_id"] in glob_ids and not event["is_error"]:
                glob_ok = True
            if event["type"] == "result":
                final = event
        passed = final and final["status"] == "success"
        if args.prompt == DEFAULT_PROMPT:
            print("默认任务 Glob 验收：", glob_ok)
            passed = passed and glob_ok
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
