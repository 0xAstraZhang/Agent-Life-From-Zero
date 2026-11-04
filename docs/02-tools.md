# 第 2 章：工具系统，Claude Code 的六件核心工具

## 导读

第 1 章的工具只是两个裸函数。这一章照着 Claude Code，把工具做成一套**系统**：每个工具除了“函数 + 说明书”，还要回答 harness 关心的几个问题：会不会改变外部世界？能不能和别的工具并行？执行前要不要问用户？权限规则该匹配它的哪个参数？

然后实现 Claude Code 最核心的六件工具：**Read、Write、Edit、Glob、Grep、Bash**，并把主循环升级成最终的结构。从这一章起，minicode 就是一个能在终端里交互、真正能改代码的 agent 了。

学完你会有：

- `minicode/tool.py`：工具基类、参数校验、文件工具共用的检查。
- `minicode/tools/`：六件核心工具。
- `minicode/agent.py`（完整版）：并行执行只读工具、一次工具调用的完整流水线，以及给后面各章预留的接缝。
- `minicode/app.py`、`minicode/cli.py`、`minicode/__main__.py`：组装和命令行界面，`python -m minicode` 可以用了。

## Claude Code 是怎么做的

### 工具清单和“是否需要权限”

Claude Code 的[工具列表文档](https://code.claude.com/docs/en/tools-reference)给每个内置工具标了一列 “Permission Required”。这一章涉及的几个：

| 工具 | 做什么 | 需要权限 |
| --- | --- | --- |
| Read | 读文件内容 | 否 |
| Glob | 按通配符找文件 | 否 |
| Grep | 搜索文件内容 | 否 |
| Edit | 对文件做精确的字符串替换 | 是 |
| Write | 新建或覆盖文件 | 是 |
| Bash | 执行 shell 命令 | 是 |

规律很清楚：**只读的不用问，会改变外部世界的要问**。minicode 用工具上的 `read_only` 属性表达这件事。

### 工具的设计细节

在 Claude Code 里，模型看到的工具说明（description）写得非常具体。下面这些行为都写在说明里，你用 Claude Code 时也能直接观察到：

- **Read** 返回带行号的内容（像 `cat -n`），默认最多读 2000 行，大文件用 offset 和 limit 分段读。
- **Edit** 是精确的字符串替换：`old_string` 必须和文件内容逐字一致，而且在文件里唯一，否则编辑失败；想全部替换要显式设 `replace_all`。
- **Edit 和 Write 都要求先 Read**：没读过就改，或者读过之后文件被别人改了，会直接报错，要求重新读。
- **Glob** 的结果按修改时间排序，**Grep** 基于 ripgrep，支持“只列文件”“列出匹配行”“只计数”几种输出。
- **Bash** 有超时（默认 2 分钟，最长 10 分钟），说明里还明确要求：找文件用 Glob、搜内容用 Grep、读文件用 Read，不要用 `find`、`grep`、`cat`。

这些看起来琐碎的规定，每一条都对应一种真实的失败：

| 规定 | 防的是什么 |
| --- | --- |
| 读出来带行号 | 模型引用代码、定位修改时有准确的坐标 |
| 改之前必须读 | 模型凭记忆“想象”文件内容，然后整个覆盖掉 |
| 读过之后被改了要重读 | 用户或格式化工具同时在改，模型用旧内容覆盖了新内容 |
| `old_string` 必须唯一 | 改错地方。唯一性迫使模型带上足够的上下文 |
| 用 Edit 而不是整个重写 | 输出 token 少，改动范围小，出错时影响也小 |
| 用专门的搜索工具而不是 Bash | 结果格式固定、有数量上限，不用每次都问用户要权限 |

### 并行执行

模型一次回复里可以发出好几个工具调用。Agent SDK 文档写明了规则：

> Read-only tools (like `Read`, `Glob`, `Grep`, and MCP tools marked as read-only) can run concurrently. Tools that modify state (like `Edit`, `Write`, and `Bash`) run sequentially to avoid conflicts.

只读工具并行，改状态的工具串行。比如模型想同时读五个文件，并行能省下不少等待时间；而两个 Edit 同时改一个文件就会互相覆盖。

## 第 1 步：工具基类

新建 `minicode/tool.py`：

```python
"""工具系统的地基：每个工具都是一个 Tool 子类。

一个工具除了“函数 + JSON Schema”，还要回答几个 harness 关心的问题：
  - read_only：会不会改变外部世界？只读工具可以并行执行、plan 模式可用、默认不用问用户。
  - needs_permission：执行前要不要过权限检查？默认等于“不是只读”。
  - permission_key：权限规则匹配什么？Bash 匹配命令，文件工具匹配路径，WebFetch 匹配域名。
"""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


class ToolError(Exception):
    """工具发现的、模型能自己修正的问题：文件不存在、old_string 不唯一……"""


@dataclass
class Decision:
    behavior: str          # allow / deny / ask
    reason: str = ""


@dataclass
class ToolContext:
    cwd: Path
    agent: object = None                                   # 需要访问 agent 的工具（子 agent、计划审批）用它
    read_files: dict[str, float] = field(default_factory=dict)  # 路径 -> 读取时的修改时间
    todos: list[dict] = field(default_factory=list)        # 第 7 章 TodoWrite 的状态


class Tool:
    name: str = ""
    description: str = ""
    parameters: dict = {"type": "object", "properties": {}}
    read_only: bool = False

    @property
    def needs_permission(self) -> bool:
        return not self.read_only

    def permission_key(self, args: dict, ctx: ToolContext) -> str | None:
        return None

    def call(self, args: dict, ctx: ToolContext) -> str | ToolResult:
        raise NotImplementedError

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


# ---------- 参数校验：模型写的参数不可信，执行前先对照 schema 检查 ----------
_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict}


def validate_args(schema: dict, args) -> str | None:
    """只检查最常见的问题：不是对象、缺必填、类型不对、枚举值不对。返回错误描述，没问题返回 None。"""
    if not isinstance(args, dict):
        return "参数必须是 JSON 对象"
    props = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            return f"缺少必填参数 {name}"
    for name, value in args.items():
        spec = props.get(name)
        if spec is None:
            continue                       # 多余参数在后面调用时会报错，这里不拦
        expected = _TYPES.get(spec.get("type"))
        is_bool = isinstance(value, bool)
        if expected and (not isinstance(value, expected) or (is_bool and spec.get("type") != "boolean")):
            return f"参数 {name} 应该是 {spec['type']}，实际是 {type(value).__name__}"
        if "enum" in spec and value not in spec["enum"]:
            return f"参数 {name} 只能取 {spec['enum']}"
    return None


# ---------- 文件工具共用的小函数 ----------
def resolve_path(ctx: ToolContext, path: str) -> Path:
    """相对路径按工作目录解析；~ 展开成家目录。返回规范化的绝对路径。"""
    p = Path(os.path.expanduser(path))
    return (p if p.is_absolute() else ctx.cwd / p).resolve()


def check_fresh(ctx: ToolContext, path: Path) -> None:
    """修改已有文件前的两道检查：读过吗？读过之后被别人改过吗？"""
    seen = ctx.read_files.get(str(path))
    if seen is None:
        raise ToolError("还没有读过这个文件。先用 Read 读取，再修改。")
    if path.stat().st_mtime > seen:
        raise ToolError("文件在你读取之后被修改过（可能是用户或格式化工具）。请重新 Read 后再改。")


def truncate(text: str, limit: int) -> str:
    """保留开头和结尾，截掉中间。报错信息常在结尾，所以不能只留开头。"""
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n...[中间省略 {len(text) - limit} 个字符]...\n\n{text[-half:]}"
```

几个设计点：

- **`call()` 可以返回字符串，也可以返回 `ToolResult`**。大多数工具成功时返回字符串就行；需要标记“这是个错误结果”时返回 `ToolResult(..., is_error=True)`，比如 Bash 命令的退出码不是 0。
- **`ToolError` 和普通异常分开**。`ToolError` 是工具“预料之中”的失败，消息是写给模型看的，要告诉它怎么改；普通异常是工具自己的 bug，主循环也会兜住，但消息对模型就没那么有用了。
- **`validate_args` 在执行前检查参数**。模型写的参数不可信：缺字段、类型不对（比如路径传了个数字）都很常见。在执行前拦下来，给出清楚的错误，比让函数抛一个看不懂的 `TypeError` 好得多。
- **`ToolContext` 是工具的共享状态**：工作目录、读过哪些文件，以及后面章节要用的待办清单。工具本身不保存状态，同一个工具对象可以给多个 agent 用（第 8 章的子 agent 就是这样）。
- **`truncate` 保留头尾、截掉中间**。命令输出的报错信息往往在最后，只保留开头就把最重要的部分丢了。

## 第 2 步：文件工具

新建 `minicode/tools/files.py`：

```python
"""文件工具：Read、Write、Edit。设计照着 Claude Code 的同名工具。"""

from ..tool import Tool, ToolError, check_fresh, resolve_path

MAX_LINES = 2000          # 默认最多读多少行
MAX_LINE_CHARS = 2000     # 单行太长（压缩过的 js、数据文件）就截断


class Read(Tool):
    name = "Read"
    description = ("读取文本文件，返回带行号的内容（每行格式：行号<TAB>内容）。默认读前 2000 行，"
                   "大文件用 offset 和 limit 分段读。修改一个已有文件之前，必须先用 Read 读过它。")
    parameters = {"type": "object", "properties": {
        "file_path": {"type": "string", "description": "文件路径，绝对路径或相对于工作目录"},
        "offset": {"type": "integer", "description": "从第几行开始读（从 1 开始），可选"},
        "limit": {"type": "integer", "description": "最多读几行，可选，默认 2000"}},
        "required": ["file_path"]}
    read_only = True

    def permission_key(self, args, ctx):
        return str(resolve_path(ctx, args["file_path"]))

    def call(self, args, ctx):
        path = resolve_path(ctx, args["file_path"])
        if not path.exists():
            raise ToolError(f"文件不存在：{path}")
        if path.is_dir():
            raise ToolError(f"{path} 是目录。用 Glob 或 Bash 的 ls 查看目录内容。")
        data = path.read_bytes()
        if b"\0" in data[:8000]:
            raise ToolError("这是二进制文件，Read 只能读文本。")
        ctx.read_files[str(path)] = path.stat().st_mtime      # 记下“读过了，以及读的时候它长什么样”
        lines = data.decode("utf-8", errors="replace").splitlines()
        if not lines:
            return "<文件存在，但内容为空>"
        start = max(args.get("offset") or 1, 1)
        limit = args.get("limit") or MAX_LINES
        chunk = lines[start - 1:start - 1 + limit]
        out = "\n".join(f"{n:>6}\t{line[:MAX_LINE_CHARS]}" for n, line in enumerate(chunk, start))
        if start - 1 + limit < len(lines):
            out += f"\n...（文件共 {len(lines)} 行，用 offset={start + limit} 继续读）"
        return out


class Write(Tool):
    name = "Write"
    description = ("把内容写入文件，文件存在就整个覆盖。改已有文件请优先用 Edit；"
                   "覆盖已有文件之前必须先 Read。")
    parameters = {"type": "object", "properties": {
        "file_path": {"type": "string", "description": "文件路径"},
        "content": {"type": "string", "description": "完整的文件内容"}},
        "required": ["file_path", "content"]}

    def permission_key(self, args, ctx):
        return str(resolve_path(ctx, args["file_path"]))

    def call(self, args, ctx):
        path = resolve_path(ctx, args["file_path"])
        existed = path.exists()
        if existed:
            check_fresh(ctx, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args["content"], encoding="utf-8")
        ctx.read_files[str(path)] = path.stat().st_mtime      # 自己写的内容，算“读过”
        verb = "覆盖" if existed else "新建"
        return f"已{verb} {path}（{len(args['content'].splitlines())} 行）"


class Edit(Tool):
    name = "Edit"
    description = ("对文件做精确的字符串替换。old_string 必须和文件内容逐字一致（包括缩进和空白），"
                   "并且在文件里只出现一次；想替换所有出现的位置就设 replace_all=true。编辑前必须先 Read。")
    parameters = {"type": "object", "properties": {
        "file_path": {"type": "string", "description": "文件路径"},
        "old_string": {"type": "string", "description": "要被替换的原文"},
        "new_string": {"type": "string", "description": "替换后的新内容"},
        "replace_all": {"type": "boolean", "description": "是否替换所有出现的位置，默认 false"}},
        "required": ["file_path", "old_string", "new_string"]}

    def permission_key(self, args, ctx):
        return str(resolve_path(ctx, args["file_path"]))

    def call(self, args, ctx):
        path = resolve_path(ctx, args["file_path"])
        if not path.exists():
            raise ToolError(f"文件不存在：{path}。新建文件请用 Write。")
        check_fresh(ctx, path)
        old, new = args["old_string"], args["new_string"]
        if old == new:
            raise ToolError("old_string 和 new_string 完全一样，没有需要修改的内容。")
        text = path.read_text(encoding="utf-8")
        count = text.count(old) if old else 0
        if count == 0:
            raise ToolError("文件里找不到 old_string。请重新 Read，逐字复制原文（注意缩进和空白）。")
        if count > 1 and not args.get("replace_all"):
            raise ToolError(f"old_string 在文件里出现了 {count} 次。请多带几行上下文让它唯一，"
                            "或者设 replace_all=true 全部替换。")
        replaced = count if args.get("replace_all") else 1
        pos = text.index(old)
        text = text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        ctx.read_files[str(path)] = path.stat().st_mtime
        return f"已修改 {path}，替换了 {replaced} 处。修改后的片段：\n{_snippet(text, pos, new)}"


def _snippet(text: str, pos: int, new: str, context: int = 3) -> str:
    """把改动位置前后几行带行号返回，模型不用再读一遍就能确认改对了。"""
    lines = text.splitlines()
    first = text.count("\n", 0, pos)
    last = first + new.count("\n")
    lo, hi = max(first - context, 0), min(last + context + 1, len(lines))
    return "\n".join(f"{n + 1:>6}\t{lines[n]}" for n in range(lo, hi))
```

`ctx.read_files` 记的是“读文件那一刻的修改时间”。`check_fresh` 比较当前的修改时间：变新了，说明在模型读过之后有人动过这个文件，必须重读。Write 和 Edit 成功后会更新这个时间，所以模型连续改同一个文件不需要每次都重读。

Edit 成功后返回改动位置前后几行（带行号），模型不用再 Read 一遍就能确认改对了。

## 第 3 步：搜索工具

新建 `minicode/tools/search.py`：

```python
"""搜索工具：Glob 找文件，Grep 搜内容。

Claude Code 不给代码库建向量索引，而是让模型像人一样用 glob/grep 现查现读。
Claude Code 的 Grep 基于 ripgrep；这里用纯 Python 实现，慢一些，但不依赖外部程序。
"""
import fnmatch
import re
from pathlib import Path

from ..tool import Tool, ToolError, resolve_path

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache"}
MAX_FILE_BYTES = 1_000_000


def _walk(base: Path):
    """遍历 base 下的文件，跳过依赖和缓存目录。"""
    if base.is_file():
        yield base
        return
    for p in sorted(base.rglob("*")):
        if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(base).parts):
            yield p


class Glob(Tool):
    name = "Glob"
    description = ("按通配符查找文件，例如 **/*.py、src/**/test_*.py。结果按修改时间从新到旧排列，"
                   "最多返回 100 个。找文件请用它，不要用 Bash 的 find 或 ls -R。")
    parameters = {"type": "object", "properties": {
        "pattern": {"type": "string", "description": "通配符，例如 **/*.md"},
        "path": {"type": "string", "description": "在哪个目录下找，默认工作目录"}},
        "required": ["pattern"]}
    read_only = True

    def permission_key(self, args, ctx):
        return str(resolve_path(ctx, args.get("path") or "."))

    def call(self, args, ctx):
        base = resolve_path(ctx, args.get("path") or ".")
        if not base.is_dir():
            raise ToolError(f"目录不存在：{base}")
        files = [p for p in base.glob(args["pattern"])
                 if p.is_file() and not SKIP_DIRS.intersection(p.relative_to(base).parts)]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return "没有匹配的文件"
        shown = "\n".join(str(p) for p in files[:100])
        return shown + (f"\n...（共 {len(files)} 个，只显示前 100 个）" if len(files) > 100 else "")


class Grep(Tool):
    name = "Grep"
    description = ("用正则表达式搜索文件内容。output_mode 可选：files_with_matches（默认，只列出文件路径）、"
                   "content（列出匹配行，带行号）、count（每个文件的匹配次数）。用 glob 参数过滤文件，例如 *.py。"
                   "搜内容请用它，不要用 Bash 的 grep。")
    parameters = {"type": "object", "properties": {
        "pattern": {"type": "string", "description": "正则表达式"},
        "path": {"type": "string", "description": "搜索的文件或目录，默认工作目录"},
        "glob": {"type": "string", "description": "只搜文件名匹配这个通配符的文件，例如 *.py"},
        "output_mode": {"type": "string", "enum": ["files_with_matches", "content", "count"]},
        "case_insensitive": {"type": "boolean", "description": "忽略大小写"},
        "head_limit": {"type": "integer", "description": "最多返回多少条，默认 100"}},
        "required": ["pattern"]}
    read_only = True

    def permission_key(self, args, ctx):
        return str(resolve_path(ctx, args.get("path") or "."))

    def call(self, args, ctx):
        try:
            regex = re.compile(args["pattern"], re.IGNORECASE if args.get("case_insensitive") else 0)
        except re.error as e:
            raise ToolError(f"正则表达式有误：{e}")
        base = resolve_path(ctx, args.get("path") or ".")
        if not base.exists():
            raise ToolError(f"路径不存在：{base}")
        mode = args.get("output_mode") or "files_with_matches"
        limit = args.get("head_limit") or 100
        out = []
        for f in _walk(base):
            if args.get("glob") and not fnmatch.fnmatch(f.name, args["glob"]):
                continue
            if f.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue                                    # 二进制或读不了的文件，跳过
            hits = [(n, line) for n, line in enumerate(text.splitlines(), 1) if regex.search(line)]
            if not hits:
                continue
            if mode == "content":
                out += [f"{f}:{n}:{line[:300]}" for n, line in hits]
            elif mode == "count":
                out.append(f"{f}:{len(hits)}")
            else:
                out.append(str(f))
            if len(out) >= limit:
                break
        if not out:
            return "没有匹配"
        return "\n".join(out[:limit]) + (f"\n...（只显示前 {limit} 条）" if len(out) >= limit else "")
```

这里没有向量数据库，也没有预先建好的索引。模型想找什么，就像人一样先 Glob 看看有哪些文件，再 Grep 搜关键词，最后 Read 读相关的几段。第 5 章会详细讲 Claude Code 为什么选择这种“现查现读”的方式。

`SKIP_DIRS` 跳过依赖和缓存目录。不跳过的话，一次 `**/*.py` 可能返回 `.venv` 里的几千个文件，既慢又浪费上下文。

## 第 4 步：Bash

新建 `minicode/tools/bash.py`：

```python
"""Bash 工具：在工作目录执行 shell 命令。

它是能力最强、也最危险的工具：模型能做的几乎任何事都可以通过它完成。
所以它永远要过权限检查（第 3 章），并且要有超时和输出上限。
"""
import os
import signal
import subprocess

from ..tool import Tool, ToolResult, truncate

DEFAULT_TIMEOUT, MAX_TIMEOUT = 120, 600     # 秒
MAX_OUTPUT = 30_000                         # 字符


class Bash(Tool):
    name = "Bash"
    description = ("在工作目录里执行 shell 命令，返回输出和退出码。用来跑测试、构建、git 等命令。"
                   "每次调用都是一个新的 shell，cd 和环境变量不会保留到下一次。"
                   "默认超时 120 秒，timeout 参数单位为秒，最大 600。"
                   "找文件用 Glob、搜内容用 Grep、读文件用 Read，不要用 find、grep、cat。")
    parameters = {"type": "object", "properties": {
        "command": {"type": "string", "description": "要执行的命令"},
        "timeout": {"type": "integer", "description": "超时秒数，可选"}},
        "required": ["command"]}

    def permission_key(self, args, ctx):
        return args["command"]

    def call(self, args, ctx):
        timeout = min(args.get("timeout") or DEFAULT_TIMEOUT, MAX_TIMEOUT)
        proc = subprocess.Popen(
            args["command"], shell=True, cwd=ctx.cwd, text=True, encoding="utf-8", errors="replace",
            stdin=subprocess.DEVNULL,                 # 命令等待输入时不会卡住
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"))   # 自成一个进程组，超时时连子进程一起杀
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
            out, err = proc.communicate()
            return ToolResult(truncate(f"{out}{err}", MAX_OUTPUT) + f"\n[超过 {timeout} 秒，已终止]", True)
        text = out + (f"\n[stderr]\n{err}" if err else "")
        text = truncate(text.strip(), MAX_OUTPUT)
        if proc.returncode != 0:
            return ToolResult(f"{text}\n[退出码 {proc.returncode}]", is_error=True)
        return text or "（命令执行成功，没有输出）"
```

三个容易忽略的细节：

- **`stdin=DEVNULL`**：如果命令在等输入（比如 `git commit` 没写 `-m` 时会打开编辑器），没有这一行就会一直卡住，直到超时。
- **`start_new_session` + `killpg`**：`shell=True` 时，超时后只杀 shell 进程的话，它启动的子进程（比如测试进程）还会在后台继续跑。放进单独的进程组，超时时一起杀掉。
- **退出码不是 0 就标记成错误结果**，但输出照样返回。测试失败的输出正是模型修 bug 最需要的信息。

## 第 5 步：工具注册

新建 `minicode/tools/__init__.py`：

```python
from .bash import Bash
from .files import Edit, Read, Write
from .search import Glob, Grep


def core_tools() -> list:
    """Claude Code 最核心的六个工具：读、写、改、找文件、搜内容、跑命令。"""
    return [Read(), Write(), Edit(), Glob(), Grep(), Bash()]
```

后面各章新增的工具（WebFetch、TodoWrite、Agent、Skill、MCP 工具）不放在这里，而是在 `app.py` 里按需加进去，因为它们需要额外的配置。

## 第 6 步：完整的主循环

用下面的内容**替换**整个 `minicode/agent.py`：

```python
"""Agent 主循环，对应 Claude Code 的 agentic loop。

一轮（turn）= 请求一次模型 + 执行它要的所有工具。模型不再调用工具，循环就结束。
主循环本身很短，后面各章的机制通过几个“接缝”接进来，它们是 None 时循环照样能跑：
  permissions（第 3 章）  hooks（第 4 章）  reminders、pending_context（第 5 章）
  session、compactor（第 6 章）
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

from .tool import Decision, ToolContext, ToolError, ToolResult, truncate, validate_args

MAX_RESULT_CHARS = 30_000
REJECTED = ("用户拒绝了这次工具调用。不要换一种方式绕过去做同样的事；"
            "停下来说明你想做什么、为什么，等待用户指示。")
INTERRUPTED = "[工具调用被中断，没有拿到结果，也不确定它是否已经产生影响]"


class Agent:
    def __init__(self, llm, tools, system_prompt: str, *, cwd=".", max_turns: int = 50,
                 max_budget_usd: float | None = None, price: tuple[float, float] = (0.0, 0.0),
                 permissions=None, hooks=None, session=None, compactor=None,
                 reminders=(), ask_user=None):
        self.llm = llm
        self.tools = {t.name: t for t in tools}
        self.messages: list[dict] = [{"role": "system", "content": system_prompt}]
        self.ctx = ToolContext(cwd=Path(cwd).resolve(), agent=self)
        self.max_turns, self.max_budget_usd = max_turns, max_budget_usd
        self.price = price                      # (输入, 输出) 每百万 token 的美元价格
        self.permissions, self.hooks = permissions, hooks
        self.session, self.compactor = session, compactor
        self.reminders = list(reminders)        # 每次请求前调用，返回要提醒模型的话
        self.pending_context: list[str] = []    # 附在下一条用户消息前面的上下文
        self.ask_user = ask_user or (lambda tool, args, reason: "no")   # 没人值守就一律拒绝
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self.last_usage = None                  # 最近一次请求的 usage，第 6 章估算上下文用
        self.mark = 0                           # 最近一次请求返回时 messages 的长度
        self._lock = Lock()

    # ---------- 主循环 ----------
    def query(self, prompt: str):
        """处理一条用户输入。生成器：边跑边产出事件，最后一个事件的 type 是 "result"。"""
        started = time.monotonic()
        if self.hooks:                                          # 第 4 章：UserPromptSubmit
            blocked, extra = self.hooks.user_prompt_submit(prompt)
            if blocked:
                yield self._result("blocked", blocked, 0, started)
                return
            if extra:
                self.pending_context.append(extra)
        self._append({"role": "user", "content": self._with_context(prompt)})

        stop_hook_active = False
        for turn in range(1, self.max_turns + 1):
            yield from self._before_request()                   # 第 6 章：上下文快满时压缩
            try:
                reply = self.llm.complete(self._request_messages(), self._schemas())
            except Exception as e:                              # SDK 已经重试过，到这里就是修不好的错
                yield self._result("error_during_execution", f"调用模型失败：{type(e).__name__}: {e}",
                                   turn, started)
                return
            self.add_usage(reply.usage.input_tokens, reply.usage.output_tokens)
            self.last_usage = reply.usage
            msg = reply.message
            self._append(msg)
            self.mark = len(self.messages)
            calls = msg.get("tool_calls") or []
            yield {"type": "assistant", "text": msg.get("content") or "",
                   "tool_calls": [{"id": c["id"], "name": c["function"]["name"],
                                   "arguments": c["function"].get("arguments") or ""} for c in calls]}

            if not calls:                                       # 不再调用工具 = 想结束了
                if self.hooks and (reason := self.hooks.stop(stop_hook_active)):
                    stop_hook_active = True                     # 第 4 章：Stop hook 可以要求继续
                    self._append({"role": "user", "content":
                                  f"<system-reminder>\n还不能结束：{reason}\n</system-reminder>"})
                    continue
                yield self._result("success", msg.get("content") or "", turn, started)
                return
            if self.max_budget_usd is not None and self.cost_usd() >= self.max_budget_usd:
                self.repair("[预算已用完，这次调用没有执行]")
                yield self._result("error_max_budget_usd", f"花费已达到上限 ${self.max_budget_usd}",
                                   turn, started)
                return
            for call, result in self._run_tools(calls):
                self._append({"role": "tool", "tool_call_id": call["id"], "content": _render(result)})
                yield {"type": "tool_result", "id": call["id"], "name": call["function"]["name"],
                       "content": result.content, "is_error": result.is_error}
        yield self._result("error_max_turns", f"已达到最大轮数 {self.max_turns}", self.max_turns, started)

    # ---------- 工具执行 ----------
    def _run_tools(self, calls: list[dict]):
        """连续的只读工具并行执行，其余的逐个执行；结果按原来的顺序产出。"""
        batch = []
        for call in calls + [None]:
            tool = self.tools.get(call["function"]["name"]) if call else None
            if tool is not None and tool.read_only:
                batch.append(call)
                continue
            if batch:
                with ThreadPoolExecutor(max_workers=min(len(batch), 10)) as pool:
                    yield from zip(batch, pool.map(self._run_one, batch))
                batch = []
            if call:
                yield call, self._run_one(call)

    def _run_one(self, call: dict) -> ToolResult:
        """一次工具调用的流水线：找工具 → 解析参数 → 校验 → PreToolUse → 权限 → 执行 → PostToolUse。"""
        name = call["function"]["name"]
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(f"没有名为 {name} 的工具。可用的工具：{', '.join(self.tools)}", True)
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError as e:
            return ToolResult(f"参数不是合法的 JSON：{e}", True)
        if error := validate_args(tool.parameters, args):
            return ToolResult(f"参数校验失败：{error}", True)

        hook_decision = None
        if self.hooks:                                          # 第 4 章：PreToolUse
            verdict = self.hooks.pre_tool_use(name, args)
            if verdict.decision == "deny":
                return ToolResult(f"被 hook 拦下：{verdict.reason}", True)
            if verdict.updated_input is not None:
                args = verdict.updated_input
                if error := validate_args(tool.parameters, args):
                    return ToolResult(f"hook 改写后的参数不合法：{error}", True)
            hook_decision = verdict.decision

        decision = self._decide(tool, args, hook_decision)      # 第 3 章：权限
        if decision.behavior == "ask":
            with self._lock:                                    # 并行执行的工具不能同时向用户提问
                decision = self._decide(tool, args, hook_decision)   # 排队时用户可能刚选了“总是允许”
                if decision.behavior == "ask":
                    answer = self.ask_user(name, args, decision.reason)
                    if answer == "no":
                        return ToolResult(REJECTED, True)
                    if answer == "always" and self.permissions:
                        self.permissions.remember(tool, args, self.ctx)
        if decision.behavior == "deny":
            return ToolResult(f"权限不允许：{decision.reason}", True)

        try:
            out = tool.call(args, self.ctx)
            result = out if isinstance(out, ToolResult) else ToolResult(str(out))
        except ToolError as e:                                  # 模型能修的错：原样告诉它
            result = ToolResult(str(e), True)
        except Exception as e:                                  # 工具自己的 bug 也不能让循环崩溃
            result = ToolResult(f"{type(e).__name__}: {e}", True)
        result.content = truncate(result.content, MAX_RESULT_CHARS)

        if self.hooks and (feedback := self.hooks.post_tool_use(name, args, result)):
            result.content += f"\n<system-reminder>\n{feedback}\n</system-reminder>"
        return result

    def _decide(self, tool, args: dict, hook_decision: str | None) -> Decision:
        if self.permissions:
            return self.permissions.check(tool, args, self.ctx, hook_decision)
        if hook_decision in ("allow", "ask"):
            return Decision(hook_decision, "hook 的决定")
        if not tool.needs_permission:
            return Decision("allow", "不需要权限的工具")
        return Decision("ask", f"{tool.name} 可能改变外部世界，需要你确认")

    # ---------- 接缝和小工具 ----------
    def _before_request(self):
        if self.compactor and (event := self.compactor.maybe_compact(self)):
            yield event

    def _request_messages(self) -> list[dict]:
        """把 reminders 临时附在最后一条消息后面：只影响这一次请求，不写进历史。"""
        notes = [text for remind in self.reminders if (text := remind(self))]
        if not notes:
            return self.messages
        last = dict(self.messages[-1])
        last["content"] = (last.get("content") or "") + "".join(
            f"\n\n<system-reminder>\n{n}\n</system-reminder>" for n in notes)
        return self.messages[:-1] + [last]

    def _with_context(self, prompt: str) -> str:
        if not self.pending_context:
            return prompt
        blocks = "".join(f"<system-reminder>\n{c}\n</system-reminder>\n" for c in self.pending_context)
        self.pending_context.clear()
        return blocks + prompt

    def _schemas(self) -> list[dict]:
        return [t.schema() for t in self.tools.values()]

    def _append(self, message: dict) -> None:
        self.messages.append(message)
        if self.session:                                        # 第 6 章：每条消息都落盘
            self.session.append(message)

    def repair(self, note: str = INTERRUPTED) -> None:
        """给最后一条 assistant 消息里没有结果的工具调用补上结果，否则下一次请求会报 400。"""
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i]["role"] == "assistant":
                answered = {m.get("tool_call_id") for m in self.messages[i + 1:]}
                for c in self.messages[i].get("tool_calls") or []:
                    if c["id"] not in answered:
                        self._append({"role": "tool", "tool_call_id": c["id"], "content": note})
                return

    def add_usage(self, input_tokens: int, output_tokens: int) -> None:
        with self._lock:                                        # 并行的子 agent 也会往这里加
            self.usage["input_tokens"] += input_tokens
            self.usage["output_tokens"] += output_tokens

    def cost_usd(self) -> float:
        return (self.usage["input_tokens"] * self.price[0] + self.usage["output_tokens"] * self.price[1]) / 1e6

    def _result(self, subtype: str, text: str, turns: int, started: float) -> dict:
        return {"type": "result", "subtype": subtype, "text": text, "num_turns": turns,
                "duration_ms": round((time.monotonic() - started) * 1000),
                "usage": dict(self.usage), "cost_usd": round(self.cost_usd(), 6),
                "session_id": getattr(self.session, "id", None)}


def _render(result: ToolResult) -> str:
    """出错的结果包一层标签，模型一眼能分清“工具成功返回的内容”和“工具报错”。"""
    return f"<tool_use_error>{result.content}</tool_use_error>" if result.is_error else result.content
```

这是 minicode 主循环的最终形态，后面的章节不会再改它。和第 1 章相比，多了三样东西。

**一、并行执行（`_run_tools`）。** 把一次回复里的工具调用按顺序分组：连续的只读调用凑成一批，用线程池并行执行；遇到会改东西的调用，先把手里的批次执行完，再单独执行它。结果按原来的顺序产出。比如 `[Read, Read, Edit, Read]` 会被分成 `[Read, Read]`、`[Edit]`、`[Read]` 三批。

**二、一次工具调用的完整流水线（`_run_one`）。** 顺序是：

```text
找工具 → 解析参数 JSON → 按 schema 校验 → PreToolUse hook → 权限判断 → （需要时问用户）→ 执行 → 截断 → PostToolUse hook
```

这个顺序和 Claude Code 的一致：Agent SDK 文档列出的权限检查顺序是先 hooks，再 deny / ask 规则、权限模式、allow 规则，最后才是询问用户的回调。每一步失败都变成一个 `is_error` 的结果交还给模型，主循环本身永远不会因为工具而崩溃。

**三、给后面各章留的接缝。** 主循环里有几处 `if self.hooks:`、`if self.permissions:` 之类的判断，它们现在都是 `None`，所以这一章的 agent 照样能跑：

| 接缝 | 现在的行为 | 哪一章填上 |
| --- | --- | --- |
| `permissions` | 没有权限引擎时，`_decide` 用最朴素的规则：不需要权限的工具直接放行，其余都问用户 | 第 3 章 |
| `hooks` | 不运行任何 hook | 第 4 章 |
| `reminders`、`pending_context` | 不附加任何提醒和上下文 | 第 5 章 |
| `session`、`compactor` | 消息只在内存里，上下文不压缩 | 第 6 章 |
| `max_budget_usd`、`price` | 不限制花费 | 第 11 章 |

还有几个小地方：

- **`ask_user` 返回三种答案**：`yes`（这次允许）、`always`（总是允许，第 3 章会把它变成一条规则）、`no`（拒绝）。默认的 `ask_user` 一律返回 `no`：没人值守的时候，需要确认的操作就是不做。
- **问用户时加锁，拿到锁之后再判断一次。** 并行执行的只读工具可能同时需要确认（比如第 10 章的 MCP 工具）。排在后面的调用拿到锁时，用户可能刚刚对前一个选了“总是允许”，重新判断一次就不会再问第二遍。
- **被用户拒绝时，返回的文字告诉模型“不要换一种方式绕过去”**。模型被拒绝后有时会想别的办法达到同样的目的，比如 `rm` 被拒就改用 Python 删文件，这句话就是防这个的。
- **`repair()`** 给没有结果的工具调用补上一条“被中断”的结果。用户按 Ctrl+C 打断工具执行时，消息列表会停在“有调用、没结果”的状态，不补上的话下一次请求就是 400。
- **出错的结果包在 `<tool_use_error>` 标签里**。OpenAI 格式的 tool 消息没有 `is_error` 字段，用标签让模型一眼分清“工具返回了内容”和“工具报错了”。Claude Code 的会话记录里也能看到同样的标签。

## 第 7 步：组装和命令行

新建 `minicode/app.py`：

```python
"""组装：建工具、接上各个接缝，得到一个能用的 Agent。对应 Claude Code 启动时做的事。

后面每一章都会回到这个文件多接一个零件。参数先按最终的样子写好，还没用到的暂时忽略。
"""
from pathlib import Path

from .agent import Agent
from .llm import LLM
from .tools import core_tools

SYSTEM = """你是 minicode，一个在终端里帮用户完成软件工程任务的 agent。你通过工具读写文件、执行命令。
先用 Glob、Grep、Read 弄清现状再动手；改已有文件用 Edit，改之前必须先 Read；改完要验证。"""


def build_agent(cwd=".", *, llm=None, mode=None, allowed_tools=(), disallowed_tools=(),
                resume=None, continue_session=False, max_turns=50, max_budget_usd=None,
                ask_user=None, home=None, with_mcp=True) -> Agent:
    cwd = Path(cwd).resolve()
    llm = llm or LLM()
    return Agent(llm, core_tools(), SYSTEM, cwd=cwd, max_turns=max_turns, ask_user=ask_user)
```

`build_agent` 是“组装车间”：读配置、建工具、把各个零件接到主循环上。后面每一章都会回到这里，多接一个零件。它的参数已经按最终的样子写好了，还没用到的暂时被忽略。

新建 `minicode/cli.py`。命令行界面不是本教程的重点，整个文件一次写好，后面的章节不再修改。里面的斜杠命令依赖后面章节的模块，在还没学到的章节里会提示“第 N 章之后可用”：

```python
"""命令行入口，对应 claude（交互模式）和 claude -p（非交互模式）。

    python -m minicode                          交互模式
    python -m minicode -p "任务"                跑完就退出，适合脚本和 CI
    python -m minicode -p "任务" --output-format stream-json   每个事件一行 JSON
    python -m minicode --continue               接着这个目录最近的会话

界面不是本教程的重点，这个文件在第 2 章一次写好。里面的斜杠命令依赖后面章节的模块，
所以那些模块都在用到时才导入：还没学到的章节，命令会提示你“第 N 章之后可用”。
"""
import argparse
import json
import sys

from .app import build_agent

DIM, RED, GREEN, RESET = "\033[2m", "\033[31m", "\033[32m", "\033[0m"
MODES = ("default", "acceptEdits", "plan", "dontAsk", "bypassPermissions")


def ask_user(tool: str, args: dict, reason: str) -> str:
    """权限确认。返回 yes（这次允许）/ always（总是允许）/ no（拒绝）。"""
    if tool == "ExitPlanMode":
        print(f"\n{args.get('plan', '')}\n")
    else:
        print(f"\n{RED}?{RESET} {tool}({json.dumps(args, ensure_ascii=False)[:300]})\n  {DIM}{reason}{RESET}")
    while True:
        try:
            answer = input("  [y] 允许  [a] 总是允许  [n] 拒绝 > ").strip().lower()
        except EOFError:
            return "no"
        if answer in ("y", "yes", "a", "n", "no", ""):
            return {"y": "yes", "yes": "yes", "a": "always"}.get(answer, "no")


def print_event(event: dict) -> None:
    kind = event["type"]
    if kind == "assistant":
        if event["text"] and event["tool_calls"]:
            print(f"{DIM}{event['text']}{RESET}")
        for c in event["tool_calls"]:
            print(f"{GREEN}●{RESET} {c['name']}({c['arguments'][:120]})")
    elif kind == "tool_result":
        lines = event["content"].strip().splitlines() or [""]
        color = RED if event["is_error"] else DIM
        more = f" …（共 {len(lines)} 行）" if len(lines) > 1 else ""
        print(f"  {color}⎿ {lines[0][:150]}{more}{RESET}")
    elif kind == "compact":
        state = f"{event.get('before')} → {event.get('after')} tokens" if event["ok"] else f"失败：{event['reason']}"
        print(f"{DIM}[上下文压缩 {event.get('level', '')}：{state}]{RESET}")
    elif kind == "result":
        if event["subtype"] == "success":
            print(f"\n{event['text']}\n")
        else:
            print(f"\n{RED}[{event['subtype']}] {event['text']}{RESET}\n")


# ---------- 斜杠命令：不经过模型，直接由 harness 处理 ----------
def cmd_help(agent, arg):
    print("/help  /clear  /compact [要求]  /context  /cost  /todos  /mode [模式]  /resume  /exit")
    if getattr(agent, "skills", None):
        print("skills：" + "  ".join(f"/{name}" for name in agent.skills))


def cmd_compact(agent, arg):
    if agent.compactor is None:
        return print("第 6 章实现上下文压缩之后可用")
    print_event(agent.compactor.compact(agent, "manual", arg))


def _tokens(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False)) // 3


def cmd_context(agent, arg):
    system = _tokens(agent.messages[:1])
    tools = _tokens([t.schema() for t in agent.tools.values()])
    history = _tokens(agent.messages[1:])
    total = system + tools + history
    window = agent.compactor.window if agent.compactor else 128_000
    print(f"系统提示词 ≈{system}  工具定义 ≈{tools}（{len(agent.tools)} 个）  对话历史 ≈{history}  "
          f"合计 ≈{total} / {window}（{total * 100 // window}%）")


def cmd_cost(agent, arg):
    u = agent.usage
    print(f"输入 {u['input_tokens']} tokens，输出 {u['output_tokens']} tokens，约 ${agent.cost_usd():.4f}")


def cmd_todos(agent, arg):
    try:
        from .tools.planning import render_todos
    except ImportError:
        return print("第 7 章实现待办清单之后可用")
    print(render_todos(agent.ctx.todos))


def cmd_mode(agent, arg):
    if agent.permissions is None:
        return print("第 3 章实现权限系统之后可用")
    if arg in MODES:
        agent.permissions.mode = arg
    print(f"当前权限模式：{agent.permissions.mode}（可选：{' / '.join(MODES)}）")


def cmd_resume(agent, arg):
    try:
        from .session import Session
    except ImportError:
        return print("第 6 章实现会话存储之后可用")
    for sid, first in Session.list(agent.ctx.cwd)[:10]:
        print(f"{sid}  {first}")
    print("用 python -m minicode --resume <id> 继续某个会话")


COMMANDS = {"/help": cmd_help, "/compact": cmd_compact, "/context": cmd_context, "/cost": cmd_cost,
            "/todos": cmd_todos, "/mode": cmd_mode, "/resume": cmd_resume}


def run_query(agent, prompt: str, on_event=print_event) -> dict | None:
    final = None
    try:
        for event in agent.query(prompt):
            on_event(event)
            final = event if event["type"] == "result" else final
    except KeyboardInterrupt:
        agent.repair()                       # 中断在工具执行中间时，补上结果，会话才能继续
        print(f"\n{RED}[已中断]{RESET}")
    return final


def close(agent) -> None:
    for client in getattr(agent, "mcp_clients", []):     # 第 10 章：关掉 MCP 服务器进程
        client.close()


def repl(build, agent) -> None:
    session = agent.session.id[:8] if agent.session else "未保存"
    mode = agent.permissions.mode if agent.permissions else "default"
    print(f"minicode · {agent.ctx.cwd} · 会话 {session} · 权限模式 {mode}\n输入 /help 查看命令，/exit 退出。")
    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not line:
            continue
        name, _, arg = line.partition(" ")
        if name in ("/exit", "/quit"):
            break
        if name == "/clear":                 # 新会话，干净的上下文
            close(agent)
            agent = build()
            print("已开始新会话")
        elif name in COMMANDS:
            COMMANDS[name](agent, arg.strip())
        elif name.startswith("/") and name[1:] in getattr(agent, "skills", {}):   # 第 9 章：用户触发 skill
            run_query(agent, agent.skills[name[1:]].render(arg.strip()))
        elif name.startswith("/"):
            print(f"未知命令 {name}，输入 /help 查看")
        else:
            run_query(agent, line)
    close(agent)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="minicode", description="一个照着 Claude Code 架构写的迷你 coding agent")
    parser.add_argument("prompt", nargs="?", help="任务；配合 -p 使用")
    parser.add_argument("-p", "--print", action="store_true", help="非交互模式：执行完就退出")
    parser.add_argument("--output-format", choices=["text", "json", "stream-json"], default="text")
    parser.add_argument("--cwd", default=".", help="agent 的工作目录")
    parser.add_argument("--permission-mode", choices=MODES)
    parser.add_argument("--allowed-tools", nargs="*", default=[], help='例如 "Read" "Bash(git diff *)"')
    parser.add_argument("--disallowed-tools", nargs="*", default=[])
    parser.add_argument("--max-turns", type=int, default=50)
    parser.add_argument("--max-budget-usd", type=float)
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true")
    parser.add_argument("-r", "--resume", metavar="SESSION_ID")
    a = parser.parse_args(argv)

    def build(**overrides):
        options = dict(cwd=a.cwd, mode=a.permission_mode, allowed_tools=a.allowed_tools,
                       disallowed_tools=a.disallowed_tools, max_turns=a.max_turns,
                       max_budget_usd=a.max_budget_usd, ask_user=None if a.print else ask_user)
        return build_agent(**{**options, **overrides})

    agent = build(resume=a.resume, continue_session=a.continue_session)
    if not a.print:
        repl(build, agent)
        return 0

    prompt = a.prompt if a.prompt is not None else sys.stdin.read()     # 非交互模式没人可问：ask 一律拒绝
    emit = {"text": lambda e: None,
            "json": lambda e: None,
            "stream-json": lambda e: print(json.dumps(e, ensure_ascii=False), flush=True)}[a.output_format]
    final = run_query(agent, prompt, emit)
    close(agent)
    if final is None:
        return 1
    if a.output_format == "text":
        print(final["text"])
    elif a.output_format == "json":
        print(json.dumps(final, ensure_ascii=False))
    return 0 if final["subtype"] == "success" else 1
```

新建 `minicode/__main__.py`，让 `python -m minicode` 能运行：

```python
import sys

from .cli import main

sys.exit(main())
```

界面上的约定和 Claude Code 类似：`●` 开头是一次工具调用，`⎿` 后面是结果的第一行，红色的 `?` 是权限确认。`-p` 是非交互模式，第 11、12 章会细讲。

## 跑起来

先在沙盒目录里放一个有 bug 的小项目：

```bash
cat > playground/stats.py <<'EOF'
def average(xs):
    return sum(xs) / (len(xs) + 1)
EOF
```

```bash
cat > playground/test_stats.py <<'EOF'
from stats import average

assert average([1, 2, 3]) == 2, average([1, 2, 3])
print("ok")
EOF
```

启动 minicode：

```bash
uv run python -m minicode --cwd playground
```

输入 `stats.py 有 bug，修好它并运行 test_stats.py 确认`。一次典型的过程是这样的（模型每次的路径可能不同）：

```text
● Glob({"pattern": "*.py"})
  ⎿ /…/playground/test_stats.py …（共 2 行）
● Read({"file_path": "stats.py"})
● Read({"file_path": "test_stats.py"})
  ⎿ 1	def average(xs): …（共 2 行）
  ⎿ 1	from stats import average …（共 4 行）
● Edit({"file_path": "stats.py", "old_string": "(len(xs) + 1)", "new_string": "len(xs)"})

? Edit({"file_path": "stats.py", "old_string": "(len(xs) + 1)", "new_string": "len(xs)"})
  Edit 可能改变外部世界，需要你确认
  [y] 允许  [a] 总是允许  [n] 拒绝 > y
  ⎿ 已修改 /…/playground/stats.py，替换了 1 处。修改后的片段： …（共 3 行）
● Bash({"command": "python test_stats.py"})

? Bash({"command": "python test_stats.py"})
  Bash 可能改变外部世界，需要你确认
  [y] 允许  [a] 总是允许  [n] 拒绝 > y
  ⎿ ok

修好了：average 的除数多加了 1，改成 len(xs) 后测试通过。
```

注意第二行：两个 Read 在同一次回复里发出，是并行执行的。

再做几个实验，体会工具设计的作用：

1. 让它“把 stats.py 里的 xs 都改名成 values”。观察它怎么处理 `old_string` 出现多次的情况。
2. 在它 Read 完、准备 Edit 之前，你自己在编辑器里改一下文件并保存。观察它收到“文件在你读取之后被修改过”之后的反应。
3. 在确认 Bash 时回答 `n`。观察模型收到拒绝后有没有换一种方式绕过去。

## 离线测试

删掉第 1 章的测试（它测的是旧版 `agent.py`）：

```bash
rm tests/test_ch01.py
```

新建 `tests/conftest.py`，让每个测试都用一个临时的家目录，不会读写你真实的配置：

```python
import pytest


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """每个测试用一个临时的家目录，测试不会读写你真实的 ~/.minicode。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home
```

新建 `tests/test_ch02.py`：

```python
import time

import pytest

from minicode.agent import Agent
from minicode.testing import FakeLLM, call
from minicode.tool import Tool, ToolContext, ToolError
from minicode.tools import core_tools
from minicode.tools.bash import Bash
from minicode.tools.files import Edit, Read


def test_edit_needs_fresh_read(tmp_path):
    ctx = ToolContext(cwd=tmp_path)
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n")
    with pytest.raises(ToolError, match="还没有读过"):
        Edit().call({"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx)
    assert Read().call({"file_path": "a.py"}, ctx).startswith("     1\tx = 1")
    with pytest.raises(ToolError, match="出现了 2 次"):
        Edit().call({"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"}, ctx)
    Edit().call({"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True}, ctx)
    assert (tmp_path / "a.py").read_text() == "x = 2\nx = 2\n"


def test_bash_timeout_and_exit_code(tmp_path):
    ctx = ToolContext(cwd=tmp_path)
    assert Bash().call({"command": "echo hi"}, ctx) == "hi"
    assert Bash().call({"command": "exit 3"}, ctx).is_error
    started = time.monotonic()
    assert Bash().call({"command": "sleep 5", "timeout": 1}, ctx).is_error
    assert time.monotonic() - started < 4


def test_bad_calls_become_tool_errors(tmp_path):
    llm = FakeLLM([[call("Read"), call("Read", file_path=1), call("Nope")], "好"])
    events = list(Agent(llm, core_tools(), "s", cwd=tmp_path).query("x"))
    errors = [e["content"] for e in events if e["type"] == "tool_result"]
    assert "缺少必填参数" in errors[0] and "应该是 string" in errors[1] and "没有名为 Nope" in errors[2]


class Slow(Tool):
    name, read_only = "Slow", True

    def call(self, args, ctx):
        time.sleep(0.3)
        return "ok"


def test_read_only_tools_run_in_parallel(tmp_path):
    llm = FakeLLM([[call("Slow"), call("Slow"), call("Slow")], "完成"])
    started = time.monotonic()
    list(Agent(llm, [Slow()], "s", cwd=tmp_path).query("x"))
    assert time.monotonic() - started < 0.8          # 串行要 0.9 秒


def test_writes_need_confirmation(tmp_path):
    asked = []
    llm = FakeLLM([call("Write", file_path="a.txt", content="x"), "好"])
    agent = Agent(llm, core_tools(), "s", cwd=tmp_path, ask_user=lambda t, a, r: asked.append(t) or "no")
    list(agent.query("x"))
    assert asked == ["Write"] and not (tmp_path / "a.txt").exists()
```

```bash
uv run python -m pytest tests -q
```

`test_read_only_tools_run_in_parallel` 用三个各睡 0.3 秒的只读工具验证并行：串行要 0.9 秒，并行应该在 0.8 秒内完成。

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| 工具数量 | 几十个内置工具（见工具列表文档） | 这一章 6 个，全书 11 个左右 |
| Grep | 基于 ripgrep，很快 | 纯 Python，大仓库会慢 |
| Bash | 功能更多，比如可以放到后台运行 | 每次一个新 shell，`cd` 不会保留到下一次 |
| 并行 | 只读工具并行；自定义工具可以用 `readOnlyHint` 声明可并行 | 一样，用 `read_only` 判断 |
| 文件检查点 | 每次编辑前给文件拍快照，可以按两次 Esc 回滚 | 没有（见进阶练习 3） |
| 参数校验 | 完整的 schema 校验 | 只查必填、类型和枚举 |

## 自检清单

- [ ] 能说出 Edit 的三条前置检查，以及每一条防的是什么。
- [ ] 能解释 `[Read, Read, Bash, Grep, Grep]` 会被分成几批、哪些并行。
- [ ] 能画出 `_run_one` 的流水线，并说出每一步失败时模型会收到什么。
- [ ] 知道 `ToolError` 和普通异常的区别，并能为一个新工具决定该抛哪一种。
- [ ] 在 playground 里让 minicode 修好一个 bug，并且能在输出里认出并行执行。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 模型反复 Edit 失败，提示找不到 `old_string` | 它复制了 Read 结果里的行号前缀 | 在 Read 的说明里写清格式；严重时在系统提示词里提醒“行号不属于文件内容” |
| Bash 命令卡住直到超时 | 命令在等交互输入，比如编辑器、确认提示 | 已经用 `stdin=DEVNULL` 防住；让模型加 `-y`、`-m` 这类非交互参数 |
| 一次 Glob 返回几千个文件 | 搜到了依赖目录 | 把目录加进 `SKIP_DIRS` |
| 测试里并行用例偶尔失败 | 机器太忙 | 调大阈值，或者单独运行这个测试 |
| Windows 上 Bash 工具行为不同 | `shell=True` 在 Windows 上用的是 cmd | 用 WSL，或者为 PowerShell 写一个单独的工具 |

## 进阶练习

1. 给 Read 加一个“最近读过的同一段内容不再重复返回全文”的优化：如果文件没变、范围相同，返回“内容和上次一样”。想一想这样做省了什么，又有什么风险。
2. 写一个 `LS` 工具，列出目录结构（树形，限制深度和数量）。它应该是只读的吗？
3. 实现文件检查点：Edit 和 Write 执行前，把原文件复制到 `~/.minicode/checkpoints/<会话>/`，再加一个 `/rewind` 命令恢复。
4. 让 Bash 支持 `run_in_background`：命令在后台运行，立刻返回一个 ID，再写一个工具按 ID 读取输出。

## 资料来源

- [Claude Code 工具列表](https://code.claude.com/docs/en/tools-reference)
- [How the agent loop works（Agent SDK）](https://code.claude.com/docs/en/agent-sdk/agent-loop)：并行执行的规则、result 的几种 subtype
- [Configure permissions（Agent SDK）](https://code.claude.com/docs/en/agent-sdk/permissions)：权限检查的顺序
- [Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [ripgrep](https://github.com/BurntSushi/ripgrep)
