"""显式 Python 回调。加载模块或运行命令由应用决定。"""
from copy import deepcopy

from .types import ToolCall, ToolResult


class HookRunner:
    def __init__(self, inner, *, before=None, after=None):
        self.inner, self.before, self.after = inner, before, after
        self.tools = inner.tools
        self.context = inner.context

    def specs(self):
        return self.inner.specs()

    def run(self, call: ToolCall) -> ToolResult:
        current = deepcopy(call)
        try:
            if self.before:
                # 返回 ToolResult 表示拦截；返回 ToolCall 表示改写后重新走校验和权限。
                decision = self.before(deepcopy(current))
                if isinstance(decision, ToolResult):
                    decision.call_id = call.id
                    return decision
                if decision is not None:
                    if not isinstance(decision, ToolCall) or decision.id != call.id:
                        raise ValueError("before hook 必须保留调用 ID")
                    current = decision
        except Exception as error:
            return ToolResult(call.id, f"执行前 hook 失败：{error}", True)
        result = self.inner.run(current)
        if self.after:
            try:
                # 执行后的 hook 只观察副本，不能把已发生的失败改写成成功。
                self.after(deepcopy(current), deepcopy(result))
            except Exception as error:
                result = ToolResult(call.id, result.content + f"\n执行后 hook 失败：{error}", True)
        return result
