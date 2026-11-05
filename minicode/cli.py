"""第 16 章：把已经验证过的组件组装成命令行程序；第 17 章增加 --sandbox。"""
import argparse
from contextlib import ExitStack
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import shlex
import sys
from uuid import uuid4

from .agent import Agent
from .budget import Budget, BudgetModel
from .coding_tools import build_coding_tools
from .permissions import Approval, PermissionRunner
from .providers import ModelConfig, create_model
from .testing import FakeModel, call
from .tool import ToolContext, ToolRunner
from .types import Message


def parser():
    p = argparse.ArgumentParser(description="minicode：统一内核、三种模型协议的教学 Agent")
    p.add_argument("-p", "--prompt")
    p.add_argument("--cwd", type=Path, default=Path.cwd())
    p.add_argument("--api", choices=("openai-chat", "openai-responses", "anthropic-messages"),
                   default=os.getenv("MINICODE_API", "openai-chat"))
    p.add_argument("--model", default=os.getenv("MINICODE_MODEL"))
    p.add_argument("--base-url", default=os.getenv("MINICODE_BASE_URL"))
    p.add_argument("--provider", default=os.getenv("MINICODE_PROVIDER", "custom"))
    p.add_argument("--api-key-env", default="MINICODE_API_KEY")
    p.add_argument("--max-output-tokens", type=int, default=4096)
    p.add_argument("--chat-token-field", choices=("max_tokens", "max_completion_tokens"),
                   default=os.getenv("MINICODE_CHAT_TOKEN_FIELD"),
                   help="Chat 输出上限字段；默认按 provider 选择")
    p.add_argument("--max-turns", type=int, default=20)
    p.add_argument("--token-budget", type=int, default=50_000)
    p.add_argument("--demo", action="store_true", help="运行固定的离线工具剧本，不请求模型")
    p.add_argument("--stream", action="store_true")
    p.add_argument("--json", action="store_true", help="每行一个运行事件")
    p.add_argument("--allow-tool", action="append", default=[], help="明确允许一个工具名，可重复")
    p.add_argument("--read-only", action="store_true", help="硬性禁用写工具及 Bash")
    p.add_argument("--instructions", help="显式加载工作区内的项目说明文件")
    p.add_argument("--session", type=Path, help="保存内部消息的 JSONL 文件")
    p.add_argument("--resume", action="store_true", help="恢复 --session 指定的记录")
    p.add_argument("--compact-chars", type=int, default=50_000)
    p.add_argument("--skills", type=Path, help="显式加载一个技能目录")
    p.add_argument("--subagents", action="store_true", help="启用只读子 Agent")
    p.add_argument("--mcp-command", help="显式启动一个 stdio MCP 服务，参数按 shell 引号拆分但不使用 shell")
    p.add_argument("--sandbox", action="store_true",
                   help="Linux：用 bubblewrap 运行 Bash，只有工作区可写，默认断网")
    p.add_argument("--sandbox-network", action="store_true", help="沙箱内允许网络（需配合 --sandbox）")
    p.add_argument("--sandbox-writable", action="append", default=[], type=Path,
                   help="沙箱内额外可写的目录，可重复（需配合 --sandbox）")
    return p


def build_sandbox(args, cwd):
    from .sandbox import DEFAULT_PROTECTED, Sandbox
    if not args.sandbox:
        if args.sandbox_network or args.sandbox_writable:
            raise ValueError("--sandbox-network 和 --sandbox-writable 需要同时指定 --sandbox")
        return None
    writable = [path.expanduser().resolve() for path in args.sandbox_writable]
    if not all(path.is_dir() for path in writable):
        raise ValueError("--sandbox-writable 必须是已存在的目录")
    # 会话、项目说明和技能会进入下一次请求的上下文，命令不能改写它们。
    # 它们可能在工作区外的额外可写目录里，所以全部交给 Sandbox，由它判断哪些需要只读挂载。
    def shown(path):
        path = path.resolve()
        return str(path.relative_to(cwd)) if path.is_relative_to(cwd) else str(path)
    candidates = [args.session, args.instructions and cwd / args.instructions, args.skills]
    extra = [shown(path) for path in candidates if path]
    sandbox = Sandbox(network=args.sandbox_network,
                      protected=DEFAULT_PROTECTED + tuple(dict.fromkeys(extra)),
                      writable=tuple(str(path) for path in writable),
                      hidden_env=(args.api_key_env,))
    return sandbox.check(cwd)  # 返回固定了 bwrap 绝对路径的副本；不可用时直接失败


def ask(tool: str, args: dict) -> bool:
    print(f"允许 {tool} {json.dumps(args, ensure_ascii=False)}？[y/N] ", end="", file=sys.stderr)
    try:
        return input().strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


def emit(event, *, as_json=False, streamed=False):
    if as_json:
        print(json.dumps(event, ensure_ascii=False,
                         default=lambda value: asdict(value) if is_dataclass(value) else str(value)))
    elif event["type"] == "text_delta":
        print(event["text"], end="", flush=True)
    elif event["type"] == "tool_result":
        print(f"\n[工具 {event['call_id']}] {event['content']}")
    elif event["type"] == "result":
        if streamed:
            print()
        if not streamed or event["status"] != "success":
            print(event["text"])
        print(f"[{event['status']}]", file=sys.stderr)


def run(args, stack):
    from .compact import CompactingModel
    from .context import load_project_instructions
    from .planning import TodoList
    from .session import SessionStore, run_session

    cwd = args.cwd.expanduser().resolve()
    if not cwd.is_dir():
        raise ValueError(f"工作目录不存在：{cwd}")
    if args.resume and not args.session:
        raise ValueError("--resume 必须同时指定 --session")
    if args.resume and not args.session.is_file():
        raise ValueError("要恢复的会话文件不存在")
    if args.max_turns <= 0 or args.compact_chars <= 0:
        raise ValueError("max-turns 和 compact-chars 必须大于 0")
    if args.session and args.session.exists() and not args.resume:
        raise ValueError("会话文件已存在；使用 --resume 恢复，或选择新路径")
    sandbox = build_sandbox(args, cwd)
    budget = Budget(args.token_budget)
    if args.demo:
        demo_call = call("Glob", pattern="*.py")
        demo_call.id = "demo_" + uuid4().hex  # 新进程恢复会话时也不能重复旧调用 ID。
        base_model = FakeModel([demo_call, "离线循环完成：模型提出工具调用，程序执行，再把结果交回模型。"])
        source = {"api": "fake", "model": "script", "provider": "local", "base_url": ""}
        args.prompt = args.prompt or "列出工作目录中的 Python 文件"
    else:
        default_url = "https://api.anthropic.com/v1" if args.api == "anthropic-messages" else "https://api.openai.com/v1"
        config = ModelConfig(args.api, args.model or "", args.base_url or default_url,
                             os.getenv(args.api_key_env, ""), provider=args.provider,
                             max_tokens=args.max_output_tokens, chat_token_field=args.chat_token_field)
        base_model = create_model(config)
        stack.callback(base_model.close)
        source = base_model.source
    model = BudgetModel(base_model, budget)
    tools = build_coding_tools(include_bash=True)
    if sandbox is not None:
        from .sandbox import sandbox_tools
        tools = sandbox_tools(tools, sandbox)
    tools.append(TodoList().tool())
    if args.skills:
        from .skills import SkillLibrary
        if not args.skills.is_dir():
            raise ValueError("指定的技能目录不存在")
        library = SkillLibrary(args.skills)
        tools.append(library.tool())
    if args.mcp_command:
        from .mcp import MCPClient
        client = stack.enter_context(MCPClient(shlex.split(args.mcp_command), cwd=cwd))
        tools.extend(client.list_tools())
    approval = Approval(ask if args.prompt is None and sys.stdin.isatty() else None)
    if args.subagents:
        from .subagents import subagent_tool
        def child_runner(context):
            return PermissionRunner(ToolRunner(build_coding_tools(include_bash=False), ToolContext(cwd)),
                                    approval=approval, read_only=True)
        tools.append(subagent_tool(lambda: model, child_runner, max_turns=6))
    runner = PermissionRunner(ToolRunner(tools, ToolContext(cwd)), approval=approval,
                              read_only=args.read_only, allowed=tuple(args.allow_tool))
    system = "你是 minicode。先检查文件，再修改，最后验证结果。工具失败时根据错误修正；不要声称完成尚未验证的工作。"
    if args.instructions:
        if not (cwd / args.instructions).is_file():
            raise ValueError("指定的项目说明文件不存在")
        system += "\n\n" + load_project_instructions(cwd, args.instructions)
    if args.skills:
        system += "\n\n" + library.catalog()
    store = SessionStore(args.session, source=source) if args.session else None
    history = store.load() if args.resume else None
    if args.resume and not history:
        raise ValueError("会话中没有可恢复的消息")
    if history is not None and (args.instructions or args.skills):
        # 恢复默认沿用旧说明；显式选择新上下文时替换应用的 system 消息。
        history = [Message.text("system", system)] + [m for m in history if m.role != "system"]
    agent = Agent(CompactingModel(model, threshold_chars=args.compact_chars), runner,
                  system_prompt=system, max_turns=args.max_turns, history=history, stream=args.stream)
    def query(prompt):
        events = run_session(agent, prompt, store) if store else agent.run(prompt)
        final = None
        for event in events:
            if event["type"] == "result":
                event["budget_usage"] = asdict(budget.usage)
            emit(event, as_json=args.json, streamed=args.stream)
            if event["type"] == "result":
                final = event
        return 0 if final and final["status"] == "success" else 1
    if args.prompt is not None:
        return query(args.prompt)
    while True:
        try:
            prompt = input("minicode> ").strip()
        except EOFError:
            return 0
        if prompt in {"exit", "quit"}:
            return 0
        if prompt:
            query(prompt)


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        with ExitStack() as stack:
            return run(args, stack)
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"minicode: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
