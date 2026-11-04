"""第 7 章：审批属于工具执行边界，模型协议适配器不参与。"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from threading import RLock

from jsonschema import Draft202012Validator

from .types import ToolCall, ToolResult


class Approval:
    """多个 runner 可共享同一审批对象，确保终端提问不会交错。"""

    def __init__(self, callback: Callable[[str, dict], bool] | None = None):
        self.callback = callback
        self._lock = RLock()

    def ask(self, name: str, arguments: dict) -> bool:
        with self._lock:
            return self.callback(name, arguments) is True if self.callback else False


class PermissionRunner:
    def __init__(self, inner, approval: Approval | None = None, *, read_only: bool = False,
                 allowed: Iterable[str] = ()):
        self.inner = inner
        self.approval = approval or Approval()
        self.read_only = read_only
        self.allowed = frozenset(allowed)
        self.context = inner.context
        self.tools = inner.tools

    def specs(self):
        # 只读模式不向模型公布可写工具；run 再检查一次，防止旧历史继续发出调用。
        return [spec for spec in self.inner.specs()
                if not self.read_only or self.tools[spec.name].read_only]

    def run(self, call: ToolCall) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None or call.parse_error or not isinstance(call.arguments, dict):
            return self.inner.run(call)
        if next(Draft202012Validator(tool.spec.parameters).iter_errors(call.arguments), None):
            return self.inner.run(call)  # 无效参数不应先打扰用户审批。
        if self.read_only and not tool.read_only:
            return ToolResult(call.id, "只读模式不允许执行这个工具", True)
        if tool.read_only or call.name in self.allowed:
            return self.inner.run(call)
        if not self.approval.ask(call.name, call.arguments):
            return ToolResult(call.id, "用户未授权此次操作；不要改用其他工具绕过拒绝", True)
        return self.inner.run(call)
