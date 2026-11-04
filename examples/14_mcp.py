"""第 14 章离线演示：显式启动本仓库的 echo 服务器，再接入统一工具执行器。"""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from minicode.agent import Agent
from minicode.mcp import MCPClient
from minicode.permissions import PermissionRunner
from minicode.testing import FakeModel, call
from minicode.tool import ToolContext, ToolRunner

server = Path(__file__).with_name("mcp_demo_server.py")
with TemporaryDirectory() as directory:
    with MCPClient([sys.executable, str(server)], name="demo") as client:
        tools = client.list_tools()
        runner = PermissionRunner(ToolRunner(tools, ToolContext(Path(directory))),
                                  allowed=["mcp__demo__echo"])
        model = FakeModel([call("mcp__demo__echo", text="你好，MCP"), "外部工具返回了问候。"])
        for event in Agent(model, runner).run("调用 echo 工具"):
            if event["type"] == "tool_result":
                print(event["content"])
            elif event["type"] == "result":
                print(event["status"], event["text"])
    print("服务器已退出:", client.proc.poll() is not None)
