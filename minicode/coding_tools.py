"""第 6 章：文件工具约束路径，Bash 只约束时间，不提供操作系统沙箱。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import signal
import subprocess
import tempfile
from threading import RLock

from .tool import Tool, ToolContext
from .types import ToolResult, ToolSpec

MAX_FILE_BYTES = 1_000_000
MAX_OUTPUT = 20_000
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".minicode"}


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + f"\n...[省略 {len(text) - limit} 个字符]...\n" + text[-half:]


def resolve_inside(ctx: ToolContext, name: str) -> Path:
    root = ctx.cwd.resolve()
    path = Path(name).expanduser()
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("路径超出工作目录；文件工具不允许访问该路径")
    return path


def _read(path: Path) -> bytes:
    if not path.is_file():
        raise ValueError("路径不是已有的普通文件")
    with path.open("rb") as handle:
        data = handle.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"文件超过 {MAX_FILE_BYTES} 字节，教学版不读取")
    if b"\0" in data:
        raise ValueError("只支持 UTF-8 文本文件")
    data.decode("utf-8")
    return data


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _files(ctx: ToolContext):
    root = ctx.cwd.resolve()
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(base) / d).is_symlink())
        for name in sorted(names):
            path = Path(base) / name
            # 不跟随文件符号链接；Read 仍可显式读取指向根目录内的链接。
            if not path.is_symlink() and path.is_file():
                yield path


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def build_coding_tools(*, include_bash: bool = True) -> list[Tool]:
    """每个工作会话新建一套工具，让读取记录不在不同 Agent 之间串用。"""
    seen: dict[Path, str] = {}
    lock = RLock()

    def read(args, ctx):
        path = resolve_inside(ctx, args["path"])
        with lock:
            data = _read(path)
            seen[path] = _digest(data)
        lines = data.decode("utf-8").splitlines()
        return truncate("\n".join(f"{i:>5}\t{line}" for i, line in enumerate(lines, 1))) or "[空文件]"

    def fresh(path):
        data = _read(path)
        if path not in seen:
            raise ValueError("修改已有文件前必须先 Read")
        if seen[path] != _digest(data):
            raise ValueError("文件内容已经改变，请重新 Read")
        return data.decode("utf-8")

    def write(args, ctx):
        path = resolve_inside(ctx, args["path"])
        data = args["content"].encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("写入内容超过教学版文件大小限制")
        with lock:
            if path.exists():
                fresh(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            seen[path] = _digest(data)
        return f"已写入 {path.relative_to(ctx.cwd.resolve())}"

    def edit(args, ctx):
        path = resolve_inside(ctx, args["path"])
        old, new = args["old"], args["new"]
        if not old or old == new:
            raise ValueError("old 必须非空，且与 new 不同")
        with lock:
            text = fresh(path)
            count = text.count(old)
            if count != 1:
                raise ValueError(f"old 必须唯一，实际出现 {count} 次；请增加上下文")
            data = text.replace(old, new, 1).encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise ValueError("修改后内容超过教学版文件大小限制")
            path.write_bytes(data)
            seen[path] = _digest(data)
        return f"已修改 {path.relative_to(ctx.cwd.resolve())}"

    def glob(args, ctx):
        pattern = args["pattern"]
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts:
            raise ValueError("pattern 必须是工作目录内的相对模式")
        matches = []
        for path in _files(ctx):
            relative = path.relative_to(ctx.cwd.resolve())
            if relative.match(pattern):
                matches.append(str(relative))
                if len(matches) >= 500:
                    matches.append("[达到 500 个结果上限]")
                    break
        return truncate("\n".join(matches)) or "[没有匹配的文件]"

    def grep(args, ctx):
        matches = []
        for path in _files(ctx):
            try:
                text = _read(path).decode("utf-8")
            except (OSError, ValueError, UnicodeError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if args["text"] in line:
                    matches.append(f"{path.relative_to(ctx.cwd.resolve())}:{number}:{line}")
                    if len(matches) >= 100:
                        return truncate("\n".join(matches) + "\n[达到 100 条结果上限]")
        return truncate("\n".join(matches)) or "[没有匹配的内容]"

    path = {"type": "string", "minLength": 1, "description": "相对工作目录的文件路径"}
    text = {"type": "string"}
    definitions = [
        ("Read", "读取 UTF-8 文本，返回行号；修改已有文件前先读取。", {"path": path}, ["path"], read, True),
        ("Write", "新建或覆盖文件；覆盖前必须 Read。", {"path": path, "content": text}, ["path", "content"], write, False),
        ("Edit", "精确替换唯一的 old 文本；修改前必须 Read。", {"path": path, "old": text, "new": text}, ["path", "old", "new"], edit, False),
        ("Glob", "按 pathlib 相对模式列出文件，例如 *.py 或 src/*.py。", {"pattern": {"type": "string", "minLength": 1}}, ["pattern"], glob, True),
        ("Grep", "在文本文件中搜索字面字符串，返回路径、行号和匹配行。", {"text": {"type": "string", "minLength": 1}}, ["text"], grep, True),
    ]
    tools = [Tool(ToolSpec(name, description, _schema(props, required)), execute,
                  read_only=read_only, parallel_safe=read_only)
             for name, description, props, required, execute, read_only in definitions]
    if include_bash:
        tools.append(Tool(ToolSpec("Bash", "执行 shell 命令。可以修改系统；需要权限确认，cwd 不是沙箱。",
                                  _schema({"command": {"type": "string", "minLength": 1},
                                           "timeout": {"type": "number", "minimum": 0.05, "maximum": 120}},
                                          ["command"])), _bash))
    return tools


def _bash(args: dict, ctx: ToolContext) -> ToolResult:
    timeout = args.get("timeout", 30)
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(args["command"], shell=True, cwd=ctx.cwd,
                                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                start_new_session=os.name == "posix")
        timed_out = False
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
        except BaseException:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
            raise
        size = output.tell()
        output.seek(0)
        if size <= MAX_OUTPUT:
            data = output.read()
        else:
            head = output.read(MAX_OUTPUT // 2)
            output.seek(-MAX_OUTPUT // 2, os.SEEK_END)
            data = head + b"\n...[output truncated]...\n" + output.read(MAX_OUTPUT // 2)
    result = data.decode("utf-8", errors="replace").strip()
    if timed_out:
        return ToolResult("", result + f"\n[超时 {timeout} 秒，已终止进程]", True)
    return ToolResult("", result + f"\n[退出码 {proc.returncode}]", proc.returncode != 0)
