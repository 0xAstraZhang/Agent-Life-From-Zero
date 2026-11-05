"""第 17 章：Linux 上用 bubblewrap 隔离 Bash；Write/Edit 共用同一份受保护路径。"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

from .coding_tools import resolve_inside
from .tool import Tool
from .types import ToolResult

DEFAULT_PROTECTED = (".git", ".minicode")
# 按名称移除常见密钥变量；名称匹配不是完整的密钥识别。
SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)


class SandboxUnavailable(RuntimeError):
    """当前系统不能创建沙箱；调用方应拒绝运行，而不是退回无沙箱执行。"""


@dataclass(frozen=True)
class Sandbox:
    network: bool = False
    protected: tuple[str, ...] = DEFAULT_PROTECTED  # 相对工作区，或绝对路径
    writable: tuple[str, ...] = ()                   # 工作区之外额外可写的目录
    hidden_env: tuple[str, ...] = ()                 # 名称不含 KEY 等字样、仍需移除的变量
    executable: str = "bwrap"                        # check() 返回的副本里是绝对路径

    def writable_roots(self, root: Path) -> list[Path]:
        return [Path(root).resolve(), *(Path(p).expanduser().resolve() for p in self.writable)]

    def protected_paths(self, root: Path) -> list[Path]:
        roots = self.writable_roots(root)
        paths = []
        for entry in self.protected:
            path = (roots[0] / Path(entry).expanduser()).resolve()
            if any(path.is_relative_to(writable) for writable in roots):  # 其余位置本来就是只读
                paths.append(path)
        return paths

    def protects(self, path: Path, root: Path) -> bool:
        return any(path.is_relative_to(protected) for protected in self.protected_paths(root))

    def argv(self, command: str, root: Path, environ=None) -> list[str]:
        root = Path(root).resolve()
        environ = os.environ if environ is None else environ
        # 挂载按顺序生效：后面的挂载覆盖前面的同一路径。
        argv = [self.executable, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                "--tmpfs", "/tmp"]
        for path in self.writable_roots(root):
            argv += ["--bind", str(path), str(path)]
        for path in self.protected_paths(root):
            if path.exists():  # bwrap 只能挂载已存在的路径
                argv += ["--ro-bind", str(path), str(path)]
        for name in sorted(environ):
            if SECRET_ENV.search(name) or name in self.hidden_env:
                argv += ["--unsetenv", name]
        argv += ["--unshare-pid", "--die-with-parent", "--new-session"]
        if not self.network:
            argv.append("--unshare-net")
        return argv + ["--chdir", str(root), "--", "/bin/sh", "-c", command]

    def locate(self, root: Path) -> str:
        """返回 bwrap 的绝对路径；沙箱可写目录里的同名程序一律跳过。"""
        unsafe = self.writable_roots(root)
        if Path(self.executable).is_absolute():
            candidates = [self.executable]
        else:
            candidates = [shutil.which(self.executable, path=directory)
                          for directory in os.environ.get("PATH", "").split(os.pathsep) if directory]
        for candidate in candidates:
            if candidate is None:
                continue
            path = Path(candidate).resolve()  # 同时识别指向可写目录的符号链接
            if path.is_file() and not any(path.is_relative_to(writable) for writable in unsafe):
                return str(path)
        raise SandboxUnavailable("找不到可信的 bwrap（工作区和额外可写目录里的同名程序不算），请先安装 bubblewrap")

    def check(self, root: Path) -> "Sandbox":
        """固定 bwrap 的绝对路径，再用真实参数启动一次空命令；返回可以交给 sandbox_tools 的副本。"""
        if not sys.platform.startswith("linux"):
            raise SandboxUnavailable("沙箱只支持 Linux（bubblewrap）")
        # 之后每次都执行这个路径，不再按 PATH 查找：沙箱内的命令可能在 PATH 里的可写目录放同名程序。
        ready = replace(self, executable=self.locate(root))
        try:
            result = subprocess.run(ready.argv("true", root), stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SandboxUnavailable(f"bwrap 无法启动：{error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip()[-300:] or f"退出码 {result.returncode}"
            raise SandboxUnavailable(f"bwrap 无法创建沙箱（常见原因：系统或容器禁止非特权用户命名空间）：{detail}")
        return ready

    def describe(self) -> str:
        network = "已开启" if self.network else "已断开"
        protected = "、".join(self.protected) or "无"
        writable = "、".join(["工作区", *self.writable])
        return (f"命令在 bubblewrap 沙箱中运行：只有{writable}可写，受保护路径（{protected}）只读，网络{network}；"
                "/tmp 是本条命令独立的临时目录，结束后清空；命令结束时其后台进程一并终止。")


def sandbox_tools(tools: list[Tool], sandbox: Sandbox) -> list[Tool]:
    """返回新列表：Bash 改在沙箱中执行，Write/Edit 拒绝受保护路径，其他工具不变。"""

    def bash(inner):
        def execute(args, ctx):
            if not Path(sandbox.executable).is_absolute():
                return ToolResult("", "沙箱未经 check() 固定 bwrap 的绝对路径，拒绝执行", True)
            # 只改写命令；超时、截断和进程组清理仍由原 Bash 工具负责。
            command = "exec " + shlex.join(sandbox.argv(args["command"], ctx.cwd))
            return inner(dict(args, command=command), ctx)
        return execute

    def guarded(inner):
        def execute(args, ctx):
            path = resolve_inside(ctx, args["path"])  # 先解析 .. 和符号链接，再比较
            if sandbox.protects(path, ctx.cwd):
                relative = path.relative_to(ctx.cwd.resolve())
                return ToolResult("", f"{relative} 是受保护路径，沙箱模式下不允许修改", True)
            return inner(args, ctx)
        return execute

    result = []
    for tool in tools:
        if tool.spec.name == "Bash":
            # 原说明写着“cwd 不是沙箱”，这里整体替换，避免给模型自相矛盾的规则。
            spec = replace(tool.spec, description="执行 shell 命令，需要权限确认。" + sandbox.describe())
            tool = replace(tool, spec=spec, execute=bash(tool.execute))
        elif tool.spec.name in {"Write", "Edit"}:
            tool = replace(tool, execute=guarded(tool.execute))
        result.append(tool)
    return result
