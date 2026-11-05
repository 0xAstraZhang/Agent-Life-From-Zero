"""第 17 章离线演示：受保护路径对 Write 生效；bwrap 可用时再运行真实的沙箱命令。"""
import os
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import time

from minicode.coding_tools import build_coding_tools
from minicode.sandbox import Sandbox, SandboxUnavailable, sandbox_tools
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall


def bash(runner, command):
    return runner.run(ToolCall("bash-1", "Bash", {"command": command}))


def blocked(result, marker):
    return "被拦截" if marker in result.content else "未拦截：" + result.content.strip()


with TemporaryDirectory() as directory:
    root = Path(directory)
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    sandbox = Sandbox()
    runner = ToolRunner(sandbox_tools(build_coding_tools(), sandbox), ToolContext(root))

    denied = runner.run(ToolCall("w1", "Write", {"path": ".git/config", "content": "x"}))
    print("Write .git/config:", "拒绝" if denied.is_error else "成功")
    written = runner.run(ToolCall("w2", "Write", {"path": "notes.txt", "content": "ok"}))
    print("Write notes.txt:", "拒绝" if written.is_error else "成功")

    try:
        sandbox.check(root)
    except SandboxUnavailable as error:
        print("沙箱不可用，跳过真实命令：", error)
    else:
        os.environ["DEMO_API_KEY"] = "sk-demo"  # 只在本进程中设置，演示会被移除
        probe = Path.home() / ".minicode-sandbox-probe"
        python = shlex.quote(sys.executable)
        bash(runner, "echo hello > inside.txt")
        print("工作区内写文件:", "成功" if (root / "inside.txt").exists() else "失败")
        print("写主目录:", blocked(bash(runner, f"echo x > {shlex.quote(str(probe))}"), "Read-only"))
        print("写 .git:", blocked(bash(runner, "echo x > .git/config"), "Read-only"))
        network = bash(runner, f"{python} -c \"import socket; socket.create_connection(('192.0.2.1', 80), timeout=2)\"")
        print("访问网络:", blocked(network, "unreachable"))
        print("密钥环境变量:", bash(runner, "echo ${DEMO_API_KEY:-已移除}").content.splitlines()[0])
        bash(runner, "(sleep 0.3; echo late > late.txt) &")
        time.sleep(0.6)
        print("后台进程:", "已随命令结束" if not (root / "late.txt").exists() else "仍在运行")
        if probe.exists():  # 只有沙箱失效时才会出现；不留下演示文件
            probe.unlink()
