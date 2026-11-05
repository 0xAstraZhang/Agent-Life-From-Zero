"""第 17 章：先观察只读挂载，再检查完整保护。"""
import os
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import time
import argparse
import subprocess

from minicode.coding_tools import build_coding_tools
from minicode.sandbox import Sandbox, SandboxUnavailable, sandbox_tools
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall


def bash(runner, command):
    return runner.run(ToolCall("bash-1", "Bash", {"command": command}))


def blocked(result, marker):
    return "被拦截" if marker in result.content else "未拦截：" + result.content.strip()


def enhanced():
    temp_root = "/var/tmp" if sys.platform.startswith("linux") else None
    with TemporaryDirectory(dir=temp_root) as directory:
        parent = Path(directory).resolve()
        root = parent / "workspace"
        root.mkdir()
        probe = parent / "outside.txt"
        probe.write_text("before", encoding="utf-8")
        (root / ".git").mkdir()
        (root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
        sandbox = Sandbox()
        runner = ToolRunner(sandbox_tools(build_coding_tools(), sandbox), ToolContext(root))
        denied = runner.run(ToolCall("w1", "Write", {"path": ".git/config", "content": "x"}))
        written = runner.run(ToolCall("w2", "Write", {"path": "notes.txt", "content": "ok"}))
        checks = [denied.is_error, not written.is_error]
        print("Write .git/config:", "拒绝" if denied.is_error else "失败：保护未生效")
        print("Write notes.txt:", "成功" if not written.is_error else "失败")
        try:
            sandbox = sandbox.check(root)
        except SandboxUnavailable as error:
            print("沙箱不可用，跳过真实命令：", error)
            return 0 if all(checks) else 1
        runner = ToolRunner(sandbox_tools(build_coding_tools(), sandbox), ToolContext(root))
        previous = os.environ.get("DEMO_API_KEY")
        os.environ["DEMO_API_KEY"] = "sk-demo"
        try:
            python = shlex.quote(sys.executable)
            inside = bash(runner, "echo hello > inside.txt")
            outside = bash(runner, f"echo x > {shlex.quote(str(probe))}")
            git = bash(runner, "echo x > .git/config")
            network = bash(runner, f"{python} -c \"import socket; socket.create_connection(('192.0.2.1', 80), timeout=2)\"")
            secret = bash(runner, "echo ${DEMO_API_KEY:-已移除}")
            background = bash(runner, "(sleep 0.3; echo late > late.txt) &")
            time.sleep(0.6)
            results = {
                "工作区内写文件": not inside.is_error and (root / "inside.txt").exists(),
                "工作区外保持原样": outside.is_error and probe.read_text() == "before",
                ".git 保持原样": git.is_error and (root / ".git/config").read_text() == "[core]\n",
                "网络已断开": network.is_error and "unreachable" in network.content,
                "密钥变量已移除": not secret.is_error and "已移除" in secret.content,
                "后台进程已结束": not background.is_error and not (root / "late.txt").exists(),
            }
            for name, passed in results.items():
                print(name + "：", passed)
            checks.extend(results.values())
        finally:
            if previous is None:
                os.environ.pop("DEMO_API_KEY", None)
            else:
                os.environ["DEMO_API_KEY"] = previous
        return 0 if all(checks) else 1


def mount_command(executable, root, command):
    """只用于固定命令的挂载实验，不接收模型生成的命令。"""
    return [executable, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--tmpfs", "/tmp", "--bind", str(root), str(root),
            "--unshare-pid", "--die-with-parent", "--new-session", "--unshare-net",
            "--chdir", str(root), "--", "/bin/sh", "-c", command]


def minimal():
    with TemporaryDirectory() as directory:
        parent = Path(directory).resolve()
        root = parent / "workspace"
        root.mkdir()
        outside = parent / "outside.txt"
        outside.write_text("before", encoding="utf-8")
        try:
            ready = Sandbox().check(root)
        except SandboxUnavailable as error:
            print("跳过真实 Linux 挂载实验：", error)
            return 0
        # /tmp 在沙箱里是空的，先把本实验的父目录只读挂载回来，便于观察外部写入失败。
        command = "printf inside > inside.txt; printf changed > " + shlex.quote(str(outside))
        argv = mount_command(ready.executable, root, command)
        bind_at = argv.index("--bind")
        argv[bind_at:bind_at] = ["--ro-bind", str(parent), str(parent)]
        result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, timeout=10, env={"PATH": "/usr/bin:/bin"})
        inside_ok = (root / "inside.txt").exists() and (root / "inside.txt").read_text() == "inside"
        outside_ok = outside.read_text() == "before" and result.returncode != 0
        print("工作区写入：", inside_ok)
        print("工作区外保持原样：", outside_ok)
        return 0 if inside_ok and outside_ok else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="Linux 挂载机制与完整保护")
    parser.add_argument("--step", choices=("minimal", "enhanced"), default="minimal")
    parser.add_argument("--offline", action="store_true", help="本章所有实验都不请求模型")
    args = parser.parse_args(argv)
    if args.step == "minimal":
        return minimal()
    return enhanced()


if __name__ == "__main__":
    raise SystemExit(main())
