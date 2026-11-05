"""Managed shell processes and opaque, paginated log handles.

Logs live outside the workspace and are only exposed through this manager. A
manager belongs to one CLI lifetime; close() terminates its process groups and
removes its private log directory. Sessions use pipes, not an interactive TTY.
"""
from __future__ import annotations

from dataclasses import dataclass
import codecs
import os
from pathlib import Path
import select
import signal
import subprocess
import tempfile
from threading import RLock, Thread, Timer
import time
from typing import BinaryIO
from uuid import uuid4

from .tool import ToolContext
from .types import ToolResult

MAX_PAGE = 20_000


@dataclass
class ProcessSession:
    id: str
    cwd: Path
    process: subprocess.Popen
    path: Path
    output: BinaryIO
    timer: Timer | None = None
    reaper: Thread | None = None
    reason: str = ""
    timeout: float = 0
    finished: bool = False


class ProcessManager:
    def __init__(self):
        self._directory: tempfile.TemporaryDirectory | None = None
        self._sessions: dict[str, ProcessSession] = {}
        self._lock = RLock()
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _get(self, session_id: str, ctx: ToolContext) -> ProcessSession:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.cwd != ctx.cwd.resolve():
                raise ValueError("未知或不属于当前工作区的进程/日志 ID")
            return session

    @staticmethod
    def _terminate_process(process: subprocess.Popen):
        # Kill the group even if the shell has exited: its descendants may still
        # be alive and hold stdout/stdin open.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait()

    @classmethod
    def _kill(cls, session: ProcessSession):
        if not session.finished:
            cls._terminate_process(session.process)

    def _stop(self, session: ProcessSession, reason: str):
        with self._lock:
            if not session.reason and session.process.poll() is None:
                session.reason = reason
            self._kill(session)
            self._finish(session)

    @staticmethod
    def _finish(session: ProcessSession):
        session.finished = True
        if session.timer is not None:
            session.timer.cancel()
        if session.process.stdin is not None and not session.process.stdin.closed:
            session.process.stdin.close()
        if not session.output.closed:
            session.output.close()

    def _reap(self, session: ProcessSession):
        """Reap exited shells promptly, even when the agent never polls them."""
        session.process.wait()
        with self._lock:
            self._refresh(session)

    def start(self, command: str, ctx: ToolContext, *, timeout: float = 120,
              interactive: bool = True) -> ProcessSession:
        if not isinstance(command, str) or not command:
            raise ValueError("command 必须是非空字符串")
        if timeout <= 0:
            raise ValueError("timeout 必须大于零")
        timeout = ctx.remaining(timeout)
        with self._lock:
            if self._closed:
                raise ValueError("进程管理器已关闭")
            if self._directory is None:
                self._directory = tempfile.TemporaryDirectory(prefix="minicode-logs-")
            session_id = uuid4().hex
            path = Path(self._directory.name) / session_id
            output = path.open("xb", buffering=0)
            process = None
            session = None
            try:
                process = subprocess.Popen(command, shell=True, cwd=ctx.cwd,
                                           stdin=subprocess.PIPE if interactive else subprocess.DEVNULL,
                                           stdout=output, stderr=subprocess.STDOUT,
                                           start_new_session=os.name == "posix")
                session = ProcessSession(session_id, ctx.cwd.resolve(), process, path, output,
                                         timeout=timeout)
                self._sessions[session_id] = session
                session.timer = Timer(timeout, self._stop, args=(session, "timeout"))
                session.timer.daemon = True
                session.reaper = Thread(target=self._reap, args=(session,), daemon=True,
                                        name=f"minicode-reap-{session_id[:8]}")
                session.timer.start()
                session.reaper.start()
                return session
            except BaseException:
                # Registration and watcher startup can be interrupted after
                # Popen succeeds. Cleanup must not depend on a constructed or
                # registered ProcessSession in that interval.
                if session is not None and session.timer is not None:
                    session.timer.cancel()
                try:
                    if process is not None:
                        self._terminate_process(process)
                        if process.stdin is not None and not process.stdin.closed:
                            process.stdin.close()
                finally:
                    if session is not None:
                        session.finished = True
                    self._sessions.pop(session_id, None)
                    output.close()
                    path.unlink(missing_ok=True)
                raise

    def _refresh(self, session: ProcessSession):
        if session.process.poll() is not None:
            # A completed shell must not leave background children behind.
            self._kill(session)
            self._finish(session)

    def _status(self, session: ProcessSession) -> tuple[str, bool]:
        with self._lock:
            self._refresh(session)
            if session.reason == "timeout":
                return f"超时 {session.timeout:.3g} 秒，已终止进程", True
            if session.reason == "stopped":
                return "已终止进程", True
            if session.process.returncode is None:
                return "运行中", False
            return f"退出码 {session.process.returncode}", session.process.returncode != 0

    def read_log(self, log_id: str, ctx: ToolContext, *, offset: int = 0,
                 limit: int = MAX_PAGE) -> ToolResult:
        ctx.check_deadline()
        if offset < 0 or not 1 <= limit <= MAX_PAGE:
            raise ValueError("日志 offset 必须非负，limit 必须在 1–20000 之间（字节）")
        session = self._get(log_id, ctx)
        status, error = self._status(session)
        with session.path.open("rb") as reader:
            reader.seek(offset)
            data = reader.read(limit)
        size = session.path.stat().st_size
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = decoder.decode(data, final=offset + len(data) >= size and session.process.returncode is not None)
        pending = decoder.getstate()[0]
        end = offset + len(data) - len(pending)
        if not text and pending:
            # Do not silently replace a code point split at a page boundary.
            # A caller can enlarge a deliberately tiny page and retry the same
            # byte offset without losing any log bytes.
            text = "[当前页不足以显示完整 UTF-8 字符；增大 limit 后重试同一 offset]"
        return ToolResult("", text + f"\n[{status}; log_id={session.id}; "
                          f"next_offset={end}; size={size}]", error)

    def poll(self, session_id: str, ctx: ToolContext, *, offset: int = 0,
             limit: int = MAX_PAGE, wait: float = 0) -> ToolResult:
        session = self._get(session_id, ctx)
        if wait < 0 or wait > 60:
            raise ValueError("wait 必须在 0–60 秒之间")
        if wait and session.process.poll() is None:
            try:
                session.process.wait(timeout=ctx.remaining(wait))
            except subprocess.TimeoutExpired:
                pass
            except BaseException:
                self._stop(session, "stopped")
                raise
        return self.read_log(session_id, ctx, offset=offset, limit=limit)

    def send(self, session_id: str, ctx: ToolContext, *, text: str = "", eof: bool = False) -> ToolResult:
        session = self._get(session_id, ctx)
        ctx.check_deadline()
        data = text.encode("utf-8")
        if len(data) > 65_536:
            raise ValueError("每次 stdin 写入最多 65536 字节")
        if session.process.poll() is not None or session.process.stdin is None or session.process.stdin.closed:
            raise ValueError("进程已经结束或 stdin 已关闭")
        if os.name != "posix":
            # Small pipe writes on Windows are supported; larger interactive
            # transfers need a platform-specific nonblocking pipe adapter.
            raise ValueError("BashSend 当前只支持 POSIX 非阻塞管道")
        pipe = session.process.stdin
        descriptor = pipe.fileno()
        os.set_blocking(descriptor, False)
        deadline = time.monotonic() + ctx.remaining(2)
        written = 0
        try:
            while written < len(data):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"stdin 写入超时，已写入 {written} 字节")
                if select.select([], [descriptor], [], remaining)[1]:
                    try:
                        written += os.write(descriptor, data[written:])
                    except BlockingIOError:
                        continue
            if eof:
                pipe.close()
        except BrokenPipeError as error:
            raise ValueError(f"进程 stdin 已关闭，已写入 {written} 字节") from error
        return ToolResult("", f"已写入 {written} 字节" + ("，已关闭 stdin" if eof else ""))

    def stop(self, session_id: str, ctx: ToolContext) -> ToolResult:
        session = self._get(session_id, ctx)
        self._stop(session, "stopped")
        return ToolResult("", f"已终止进程 session_id={session.id}")

    def run(self, command: str, ctx: ToolContext, *, timeout: float = 30) -> ToolResult:
        session = self.start(command, ctx, timeout=timeout, interactive=False)
        try:
            session.process.wait()
        except BaseException:
            self._stop(session, "stopped")
            raise
        status, error = self._status(session)
        size = session.path.stat().st_size
        with session.path.open("rb") as reader:
            if size <= MAX_PAGE:
                data = reader.read()
            else:
                head = reader.read(MAX_PAGE // 2)
                reader.seek(-MAX_PAGE // 2, os.SEEK_END)
                data = head + b"\n...[output truncated]...\n" + reader.read(MAX_PAGE // 2)
        text = data.decode("utf-8", errors="replace").strip()
        reference = f"; 完整日志 BashLog log_id={session.id}" if size > MAX_PAGE else ""
        return ToolResult("", text + f"\n[{status}{reference}]", error)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            sessions = list(self._sessions.values())
            for session in sessions:
                self._stop(session, "stopped")
            self._sessions.clear()
            if self._directory is not None:
                self._directory.cleanup()
        # Reapers acquire the manager lock after wait(); joining while holding
        # that lock would deadlock. The processes are already reaped above.
        for session in sessions:
            if session.reaper is not None and session.reaper.ident is not None:
                session.reaper.join()
