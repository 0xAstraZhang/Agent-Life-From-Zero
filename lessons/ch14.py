"""第 14 章：已知 echo 服务器的最小往返，再换成完整 MCP 客户端。"""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from lessons.runtime import open_model, parse_args, print_event
from minicode.agent import Agent
from minicode.mcp import MCPClient
from minicode.permissions import PermissionRunner
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import ToolSpec


@contextmanager
def echo_process():
    """只启动本仓库中固定的教学服务器。"""
    server = Path(__file__).resolve().parents[1] / "examples" / "mcp_demo_server.py"
    process = subprocess.Popen([sys.executable, str(server)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True, encoding="utf-8")
    try:
        yield process
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


def request(process, request_id, method, params):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    reply = json.loads(process.stdout.readline())
    if reply.get("id") != request_id:
        raise ValueError("响应不属于本次请求")
    if "error" in reply:
        raise ValueError(reply["error"])
    return reply["result"]


def echo_tool(process):
    request(process, 1, "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "lesson-14", "version": "1"},
    })
    process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    process.stdin.flush()
    definition = request(process, 2, "tools/list", {})["tools"][0]
    next_id = 2

    def execute(arguments, context):
        nonlocal next_id
        next_id += 1
        result = request(process, next_id, "tools/call", {
            "name": definition["name"], "arguments": arguments,
        })
        return result["content"][0]["text"]

    return Tool(ToolSpec("mcp__demo__echo", definition["description"],
                         definition["inputSchema"]), execute)


def run_agent(model, tools, root):
    runner = PermissionRunner(ToolRunner(tools, ToolContext(root)),
                              allowed=["mcp__demo__echo"])
    final, echo_ok = None, False
    for event in Agent(model, runner, max_turns=4).run(
            "必须调用 mcp__demo__echo，text 参数精确等于「你好，MCP」（不含引号）。根据工具结果回答。"):
        print_event(event)
        if event["type"] == "tool_result":
            echo_ok = echo_ok or (not event["is_error"] and event["content"].strip() == "你好，MCP")
        if event["type"] == "result":
            final = event
    print("echo 往返验收：", echo_ok)
    return 0 if final and final["status"] == "success" and echo_ok else 1


def main(argv=None):
    args = parse_args("最小 echo 桥接与完整 MCP 客户端", argv)
    script = [call("mcp__demo__echo", text="你好，MCP"), "外部工具返回了问候。"]
    with TemporaryDirectory() as directory, open_model(args, script) as model:
        root = Path(directory)
        if args.step == "minimal":
            with echo_process() as process:
                return run_agent(model, [echo_tool(process)], root)
        server = Path(__file__).resolve().parents[1] / "examples" / "mcp_demo_server.py"
        with MCPClient([sys.executable, str(server)], name="demo") as client:
            return run_agent(model, client.list_tools(), root)


if __name__ == "__main__":
    raise SystemExit(main())
