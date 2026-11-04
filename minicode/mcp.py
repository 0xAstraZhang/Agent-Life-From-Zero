"""第 14 章：MCP 2025-11-25 的 stdio/tools 子集；不自动发现或启动服务。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time

from .coding_tools import truncate
from .tool import Tool
from .types import ToolResult, ToolSpec

PROTOCOL_VERSION = "2025-11-25"
MAX_LINE = 1_000_000


class MCPError(RuntimeError):
    pass


class MCPClient:
    """显式启动命令；一个 client 串行发送请求，并持有进程直到离开 with。"""

    def __init__(self, command: list[str], *, name: str = "local", cwd: str | Path | None = None,
                 timeout: float = 10):
        if not command or timeout <= 0:
            raise ValueError("command 不得为空，timeout 必须为正")
        self.command, self.name, self.cwd, self.timeout = list(command), name, cwd, timeout
        self.proc = None
        self._lines = queue.Queue(maxsize=64)
        self._closed = threading.Event()
        self._lock = threading.RLock()
        self._next_id = 0
        self._thread = None

    def __enter__(self):
        if self.proc is not None:
            raise MCPError("每个 MCPClient 只能启动一次")
        self.proc = subprocess.Popen(self.command, cwd=self.cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     start_new_session=os.name == "posix")
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        try:
            info = self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                "clientInfo": {"name": "minicode", "version": "0.1.0"}})
            if info.get("protocolVersion") != PROTOCOL_VERSION:
                raise MCPError(f"教学客户端只支持 MCP {PROTOCOL_VERSION}")
            if "tools" not in info.get("capabilities", {}):
                raise MCPError("服务器没有声明 tools 能力")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def _put(self, value):
        while not self._closed.is_set():
            try:
                self._lines.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def _reader(self):
        try:
            while not self._closed.is_set():
                line = self.proc.stdout.readline(MAX_LINE + 1)
                if not line:
                    self._put(MCPError("MCP 服务器已关闭输出"))
                    return
                if len(line) > MAX_LINE:
                    self._put(MCPError("MCP 消息超过大小限制"))
                    return
                self._put(line)
        except (OSError, ValueError) as exc:
            self._put(MCPError(str(exc)))

    def _send(self, message: dict):
        if self.proc is None or self._closed.is_set():
            raise MCPError("MCP 会话未启动或已经关闭")
        try:
            payload = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            self.proc.stdin.write(payload)
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise MCPError(f"发送 MCP 消息失败：{exc}") from exc

    def request(self, method: str, params: dict | None = None) -> dict:
        try:
            return self._request(method, params)
        except MCPError:
            # 不能在失去同步的连接上重发有副作用的调用。
            self.close()
            raise

    def _request(self, method: str, params: dict | None = None) -> dict:
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            deadline = time.monotonic() + self.timeout
            self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            while True:
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    line = self._lines.get(timeout=remaining)
                except queue.Empty:
                    try:
                        self._send({"jsonrpc": "2.0", "method": "notifications/cancelled",
                                    "params": {"requestId": rid, "reason": "timeout"}})
                    finally:
                        self.close()
                    raise MCPError(f"{method} 超时；会话已关闭，工具是否已产生影响未知")
                if isinstance(line, Exception):
                    raise line
                try:
                    message = json.loads(line)
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise MCPError("MCP stdout 必须只包含 JSON-RPC 消息，日志应写入 stderr") from exc
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise MCPError("无效的 JSON-RPC 消息")
                if "method" in message:
                    if "id" in message:
                        response = {"jsonrpc": "2.0", "id": message["id"]}
                        if message["method"] == "ping":
                            response["result"] = {}
                        else:
                            response["error"] = {"code": -32601, "message": "Method not supported"}
                        self._send(response)
                    continue  # 本章不订阅工具列表变化等通知；截止时间不会被重置。
                if message.get("id") != rid:
                    raise MCPError("MCP 响应 ID 与请求不匹配")
                if "error" in message:
                    raise MCPError(f"{method}: {message['error']}")
                if not isinstance(message.get("result"), dict):
                    raise MCPError("MCP result 应为对象")
                return message["result"]

    def list_tools(self) -> list[Tool]:
        items, cursor, cursors = [], None, set()
        for _ in range(50):
            result = self.request("tools/list", {"cursor": cursor} if cursor else {})
            page = result.get("tools")
            if not isinstance(page, list):
                raise MCPError("tools/list 缺少 tools 数组")
            items.extend(page)
            cursor = result.get("nextCursor")
            if not cursor:
                tools = [self._tool(item) for item in items]
                names = [tool.spec.name for tool in tools]
                if len(set(names)) != len(names):
                    raise MCPError("MCP 服务器返回重复的工具名称")
                return tools
            if not isinstance(cursor, str) or cursor in cursors:
                raise MCPError("MCP 分页 cursor 重复或无效")
            cursors.add(cursor)
        raise MCPError("MCP 工具列表超过 50 页")

    def _tool(self, item: dict) -> Tool:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise MCPError("MCP 工具缺少有效名称")
        remote_name = item["name"]
        schema = item.get("inputSchema")
        if not isinstance(schema, dict):
            raise MCPError("MCP 工具缺少 inputSchema")
        if item.get("execution", {}).get("taskSupport") == "required":
            raise MCPError("教学客户端不支持要求异步 task 的 MCP 工具")
        base = f"mcp__{self.name}__{remote_name}"
        name = re.sub(r"[^A-Za-z0-9_-]", "_", base)
        if name != base or len(name) > 64:
            name = name[:53] + "_" + hashlib.sha256(base.encode()).hexdigest()[:10]

        def execute(arguments, _ctx):
            result = self.request("tools/call", {"name": remote_name, "arguments": arguments})
            parts = []
            for block in result.get("content", []):
                if block.get("type") == "text":
                    parts.append(block["text"])
                else:
                    parts.append(f"[教学版不呈现 {block.get('type', 'unknown')} 内容块]")
            if not parts and "structuredContent" in result:
                parts.append(json.dumps(result["structuredContent"], ensure_ascii=False))
            return ToolResult("", truncate("\n".join(parts) or "[空结果]"), bool(result.get("isError")))

        # readOnlyHint 是服务器的声明，不是本地信任决定。MCP 工具都经过权限审批。
        return Tool(ToolSpec(name, item.get("description") or remote_name, schema), execute,
                    read_only=False, parallel_safe=False)

    def close(self):
        if self.proc is None or self._closed.is_set():
            return
        self._closed.set()
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            self._signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                self._signal(signal.SIGKILL)
                self.proc.wait()
        # 父进程退出不代表它的子进程已退出；关闭本次会话拥有的进程组。
        if os.name == "posix":
            self._signal(signal.SIGTERM)
        if self._thread:
            self._thread.join(timeout=0.3)
            if self._thread.is_alive():
                self._signal(signal.SIGKILL)
                self._thread.join(timeout=0.5)
        self.proc.stdout.close()

    def _signal(self, signum):
        try:
            if os.name == "posix":
                os.killpg(self.proc.pid, signum)
            elif signum == signal.SIGTERM:
                self.proc.terminate()
            else:
                self.proc.kill()
        except ProcessLookupError:
            pass
