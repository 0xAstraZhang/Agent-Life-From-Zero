"""第 7 章离线演示：无人值守拒绝写入，显式允许后再执行。"""
from pathlib import Path
from tempfile import TemporaryDirectory

from minicode.coding_tools import build_coding_tools
from minicode.permissions import PermissionRunner
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall

with TemporaryDirectory() as directory:
    root = Path(directory)
    inner = ToolRunner(build_coding_tools(), ToolContext(root))
    call = ToolCall("write-1", "Write", {"path": "note.txt", "content": "hello"})
    denied = PermissionRunner(inner).run(call)
    print("默认拒绝:", denied.is_error, "文件存在:", (root / "note.txt").exists())
    permitted = PermissionRunner(inner, allowed=["Write"]).run(call)
    print("显式允许:", not permitted.is_error, "文件内容:", (root / "note.txt").read_text())
    plan = PermissionRunner(inner, read_only=True, allowed=["Bash"])
    print("只读模式工具:", ", ".join(spec.name for spec in plan.specs()))
