"""第 6 章：文件工具约束路径，Bash 只约束时间，不提供操作系统沙箱。"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from threading import RLock

from .processes import ProcessManager
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
        ctx.check_deadline()
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not (Path(base) / d).is_symlink())
        for name in sorted(names):
            path = Path(base) / name
            # 不跟随文件符号链接；Read 仍可显式读取指向根目录内的链接。
            if not path.is_symlink() and path.is_file():
                yield path


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def build_coding_tools(*, include_bash: bool = True,
                       process_manager: ProcessManager | None = None) -> list[Tool]:
    """每个工作会话新建一套工具，让读取记录不在不同 Agent 之间串用。"""
    seen: dict[Path, str] = {}
    lock = RLock()

    def read(args, ctx):
        path = resolve_inside(ctx, args["path"])
        with lock:
            data = _read(path)
            seen[path] = _digest(data)
        lines = data.decode("utf-8").splitlines()
        if not lines:
            return "[空文件]"
        offset, limit, column = args.get("offset", 1), args.get("limit", 200), args.get("column", 0)
        if offset > len(lines):
            return f"[文件共 {len(lines)} 行，offset 已超过文件末尾]"
        if column > len(lines[offset - 1]):
            raise ValueError("column 超过起始行长度")
        rendered = []
        length = 0
        next_offset, next_column = offset, column
        for number in range(offset, min(len(lines) + 1, offset + limit)):
            ctx.check_deadline()
            content = lines[number - 1][column if number == offset else 0:]
            prefix = f"{number:>5}\t"
            room = MAX_OUTPUT - 200 - length - len(prefix) - 1
            if room <= 0:
                next_offset, next_column = number, 0
                break
            piece = content[:room]
            rendered.append(prefix + piece)
            length += len(prefix) + len(piece) + 1
            if len(piece) < len(content):
                next_offset = number
                next_column = (column if number == offset else 0) + len(piece)
                break
            next_offset, next_column = number + 1, 0
        result = "\n".join(rendered)
        if next_offset <= len(lines):
            result += (f"\n[共 {len(lines)} 行；继续 Read offset={next_offset} "
                       f"column={next_column} limit={limit}]")
        return result

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
            if args.get("path") and not path.relative_to(ctx.cwd.resolve()).match(args["path"]):
                continue
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
        ("Read", "分段读取 UTF-8 文本并返回原始行号。offset 从 1 开始，limit 默认 200 行；"
         "超长行可按续读提示设置 column（从 0 开始）。修改已有文件前先读取。",
         {"path": path, "offset": {"type": "integer", "minimum": 1},
          "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
          "column": {"type": "integer", "minimum": 0}}, ["path"], read, True),
        ("Write", "新建或覆盖文件；覆盖前必须 Read。", {"path": path, "content": text}, ["path", "content"], write, False),
        ("Edit", "精确替换唯一的 old 文本；修改前必须 Read。", {"path": path, "old": text, "new": text}, ["path", "old", "new"], edit, False),
        ("Glob", "按 pathlib 相对模式列出文件，例如 *.py 或 src/*.py。", {"pattern": {"type": "string", "minLength": 1}}, ["pattern"], glob, True),
        ("Grep", "在文本文件中搜索字面字符串，返回路径、行号和匹配行，可按相对路径 glob 筛选。",
         {"text": {"type": "string", "minLength": 1}, "path": path}, ["text"], grep, True),
    ]
    tools = [Tool(ToolSpec(name, description, _schema(props, required)), execute,
                  read_only=read_only, parallel_safe=read_only)
             for name, description, props, required, execute, read_only in definitions]
    if include_bash:
        manager = process_manager or ProcessManager()
        tools.append(Tool(ToolSpec("Bash", "执行 shell 命令。可以修改系统；需要权限确认，cwd 不是沙箱。",
                                  _schema({"command": {"type": "string", "minLength": 1},
                                           "timeout": {"type": "number", "minimum": 0.05, "maximum": 120}},
                                          ["command"])),
                          lambda args, ctx: manager.run(args["command"], ctx, timeout=args.get("timeout", 30))))
        identifier = {"type": "string", "minLength": 1, "description": "当前会话返回的 opaque ID"}
        paging = {"offset": {"type": "integer", "minimum": 0},
                  "limit": {"type": "integer", "minimum": 1, "maximum": MAX_OUTPUT}}

        def start(args, ctx):
            session = manager.start(args["command"], ctx, timeout=args.get("timeout", 120))
            return f"已启动 session_id={session.id}；用 BashPoll 读取输出，用 BashSend 写入 stdin。"

        definitions = [
            ("BashStart", "启动后台 shell 进程，使用管道 stdin/stdout（无 TTY）。可以修改系统；"
             "需要权限确认，cwd 不是沙箱。超时或会话关闭会终止进程组。",
             {"command": {"type": "string", "minLength": 1},
              "timeout": {"type": "number", "minimum": 0.05, "maximum": 3600}}, ["command"], start),
            ("BashPoll", "读取当前工作区后台进程的输出。offset/limit 单位为字节，按 next_offset 续读。",
             {"session_id": identifier, **paging, "wait": {"type": "number", "minimum": 0, "maximum": 60}},
             ["session_id"], lambda args, ctx: manager.poll(ctx=ctx, **args)),
            ("BashSend", "向后台进程写入 stdin；text 不会自动追加换行，eof=true 关闭输入。仅 POSIX 管道。",
             {"session_id": identifier, "text": {"type": "string"}, "eof": {"type": "boolean"}},
             ["session_id"], lambda args, ctx: manager.send(ctx=ctx, **args)),
            ("BashStop", "终止当前工作区指定后台进程及其进程组。",
             {"session_id": identifier}, ["session_id"], lambda args, ctx: manager.stop(ctx=ctx, **args)),
            ("BashLog", "分段读取 Bash 完整日志。日志 ID 仅在当前会话有效，offset/limit 单位为字节。",
             {"log_id": identifier, **paging}, ["log_id"], lambda args, ctx: manager.read_log(ctx=ctx, **args)),
        ]
        tools.extend(Tool(ToolSpec(name, description, _schema(props, required)), execute)
                     for name, description, props, required, execute in definitions)
    return tools
