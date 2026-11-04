# 第 4 章：Hooks，在固定时刻插入你自己的代码

## 导读

权限规则只能回答“放行、拒绝、还是问”。但很多团队的要求比这细得多：

- “不管权限怎么配，`main` 分支上不许 `git push`。”
- “每次改完 Python 文件，自动跑一遍格式化，把结果告诉模型。”
- “说‘做完了’之前，必须跑过测试。”
- “每次会话开始，把今天的值班表放进上下文。”

把这些逻辑写死在 agent 里不现实：每个团队、每个项目都不一样。Claude Code 的解法是 **hooks**：在 agent 生命周期的固定时刻，运行用户配置的命令，用命令的退出码和输出来影响 agent 的行为。agent 本身不用改一行代码。

学完你会有：

- `minicode/hooks.py`：六种事件、匹配器、Claude Code 同款的 stdin JSON 和退出码协议。
- 三个实用的 hook 脚本：拦截危险命令、改完自动检查、强制先测试再结束。
- 能回答：hook 和权限规则有什么区别？为什么 hook 用退出码 2 而不是 1 表示“阻止”？

## Claude Code 是怎么做的

以下内容来自 Claude Code 的 [hooks 文档](https://code.claude.com/docs/en/hooks)。

### 事件

Claude Code 现在有三十多种 hook 事件。最常用的几种：

| 事件 | 什么时候触发 | 能做什么 |
| --- | --- | --- |
| `SessionStart` | 会话开始或恢复 | 往上下文里加内容 |
| `UserPromptSubmit` | 用户的输入交给模型之前 | 拦下这条输入，或者附加上下文 |
| `PreToolUse` | 工具执行之前 | 放行、拒绝、要求确认，或者改写参数 |
| `PostToolUse` | 工具执行之后 | 把反馈交给模型（工具已经执行过，拦不住了） |
| `Stop` | 模型准备结束这一轮 | 不让它结束，要求继续 |
| `PreCompact` | 压缩上下文之前 | 比如先把完整记录存档 |

### 协议：stdin 进，退出码和 stdout 出

hook 是一条 shell 命令。Claude Code 把“发生了什么”写成 JSON 发到它的 stdin，公共字段有 `session_id`、`transcript_path`、`cwd`、`permission_mode`、`hook_event_name`；工具相关的事件还有 `tool_name`、`tool_input`，`PostToolUse` 还有 `tool_response`。

命令用**退出码**回答：

| 退出码 | 含义 |
| --- | --- |
| 0 | 成功。stdout 如果是 JSON，按里面的字段处理 |
| 2 | 阻止。stderr 是理由：`PreToolUse` 拦下工具，`UserPromptSubmit` 拦下输入，`Stop` 不让模型结束；`PostToolUse` 拦不住，理由交给模型 |
| 其他 | hook 自己出错了，不影响 agent，继续执行 |

为什么“阻止”是 2 而不是 1？因为脚本出 bug 时最常见的退出码就是 1。如果 1 表示阻止，一个写错的 hook 就会把 agent 卡死；用一个专门的值，hook 出错时 agent 照常工作。

退出码 0 时，stdout 可以输出 JSON 做更细的控制：

```json
{"decision": "block", "reason": "给模型的说明"}
```

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "理由",
                        "updatedInput": {"command": "改写后的完整参数"}}}
```

`permissionDecision` 可以是 `allow`、`deny`、`ask`（还有一个 `defer`）。几个 hook 意见不同时，`deny` 优先于 `ask`，`ask` 优先于 `allow`。`updatedInput` 会**整个替换**原来的参数，所以要把没改的字段也带上。另外，`additionalContext` 可以把一段文字加进模型的上下文。

**hook 的 allow 打不穿权限规则。** 文档写明：PreToolUse 返回 `"allow"` 不会跳过 deny 和 ask 规则；而退出码 2 会在规则检查之前就拦下调用。

### 匹配器

`matcher` 决定一组 hook 对哪些工具生效：留空或 `*` 匹配所有；只含字母、数字和 `|`、`,` 时按名字精确匹配，比如 `Edit|Write`；含其他字符时当作正则表达式，比如 `mcp__memory__.*`。

## 第 1 步：实现 HookRunner

新建 `minicode/hooks.py`：

```python
"""Hooks：在 agent 生命周期的固定时刻运行你写的命令，协议照着 Claude Code。

配置写在 settings.json 的 "hooks" 里：
    {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python check.py"}]}]}}

命令从 stdin 收到一个 JSON（发生了什么），用退出码和 stdout 回答：
    退出码 0：成功。stdout 如果是 JSON，就按里面的字段决定；不是 JSON 时，
              SessionStart / UserPromptSubmit 的 stdout 会作为上下文交给模型
    退出码 2：阻止。stderr 是理由：PreToolUse 拦下工具，UserPromptSubmit 拦下输入，
              Stop 让模型不许结束，PostToolUse 把理由反馈给模型（工具已经执行过了）
    其他退出码：hook 自己出错，不影响 agent，只打印警告
"""
import json
import re
import subprocess
import sys
from dataclasses import dataclass

EVENTS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "PreCompact")
DEFAULT_TIMEOUT = 60      # 秒。Claude Code 的命令 hook 默认是 600 秒


@dataclass
class Verdict:
    decision: str | None = None        # PreToolUse 专用：allow / deny / ask
    reason: str = ""
    updated_input: dict | None = None  # PreToolUse 专用：改写后的完整参数
    context: str = ""                  # 要交给模型的额外上下文
    block: bool = False                # 退出码 2，或者输出了 {"decision": "block"}


def matcher_hits(matcher: str | None, value: str) -> bool:
    """空或 * 匹配所有；只含字母数字和 | , 的按名字列表精确匹配；其他按正则搜索。"""
    if not matcher or matcher == "*":
        return True
    if re.fullmatch(r"[\w\- ,|]+", matcher):
        return value in {m.strip() for m in re.split(r"[|,]", matcher)}
    return re.search(matcher, value) is not None


class HookRunner:
    def __init__(self, config: dict, cwd, session_id: str = "", transcript_path: str = "",
                 mode_getter=lambda: "default"):
        unknown = set(config) - set(EVENTS)
        if unknown:
            print(f"[hooks] 忽略不支持的事件：{', '.join(sorted(unknown))}", file=sys.stderr)
        self.config, self.cwd = config, str(cwd)
        self.session_id, self.transcript_path = session_id, transcript_path
        self.mode_getter = mode_getter

    # ---------- 底层：运行某个事件的所有 hook ----------
    def run(self, event: str, payload: dict, match_value: str | None = None) -> list[Verdict]:
        payload = {"session_id": self.session_id, "transcript_path": self.transcript_path,
                   "cwd": self.cwd, "permission_mode": self.mode_getter(),
                   "hook_event_name": event, **payload}
        verdicts = []
        for group in self.config.get(event, []):
            if match_value is not None and not matcher_hits(group.get("matcher"), match_value):
                continue
            for hook in group.get("hooks", []):
                if hook.get("type", "command") == "command":
                    verdicts.append(self._run_command(event, hook, payload))
        return verdicts

    def _run_command(self, event: str, hook: dict, payload: dict) -> Verdict:
        try:
            proc = subprocess.run(hook["command"], shell=True, cwd=self.cwd, capture_output=True,
                                  text=True, input=json.dumps(payload, ensure_ascii=False),
                                  timeout=hook.get("timeout", DEFAULT_TIMEOUT))
        except subprocess.TimeoutExpired:
            print(f"[hooks] {event} 超时：{hook['command']}", file=sys.stderr)
            return Verdict()
        if proc.returncode == 2:
            reason = proc.stderr.strip() or "（hook 没有给出理由）"
            return Verdict(decision="deny" if event == "PreToolUse" else None, reason=reason, block=True)
        if proc.returncode != 0:
            print(f"[hooks] {event} 出错（退出码 {proc.returncode}）：{proc.stderr.strip()}", file=sys.stderr)
            return Verdict()
        out = proc.stdout.strip()
        try:
            data = json.loads(out) if out.startswith("{") else None
        except json.JSONDecodeError:
            data = None
        if data is None:                                    # 纯文本输出
            return Verdict(context=out if event in ("SessionStart", "UserPromptSubmit") else "")
        specific = data.get("hookSpecificOutput") or {}
        return Verdict(decision=specific.get("permissionDecision"),
                       reason=specific.get("permissionDecisionReason") or data.get("reason", ""),
                       updated_input=specific.get("updatedInput"),
                       context=specific.get("additionalContext", ""),
                       block=data.get("decision") == "block")

    # ---------- 给主循环用的几个入口 ----------
    def session_start(self, source: str = "startup") -> str:
        return "\n".join(v.context for v in self.run("SessionStart", {"source": source}) if v.context)

    def user_prompt_submit(self, prompt: str) -> tuple[str | None, str]:
        verdicts = self.run("UserPromptSubmit", {"prompt": prompt})
        blocked = next((v.reason or "被 hook 拦下" for v in verdicts if v.block), None)
        return blocked, "\n".join(v.context for v in verdicts if v.context)

    def pre_tool_use(self, tool_name: str, tool_input: dict) -> Verdict:
        """多个 hook 意见不同时：deny > ask > allow；改写参数以最后一个为准。"""
        verdicts = self.run("PreToolUse", {"tool_name": tool_name, "tool_input": tool_input}, tool_name)
        final = Verdict()
        for v in verdicts:
            if v.updated_input is not None:
                final.updated_input = v.updated_input
        for decision in ("deny", "ask", "allow"):
            hit = next((v for v in verdicts if v.decision == decision), None)
            if hit:
                final.decision, final.reason = decision, hit.reason
                break
        return final

    def post_tool_use(self, tool_name: str, tool_input: dict, result) -> str | None:
        verdicts = self.run("PostToolUse", {"tool_name": tool_name, "tool_input": tool_input,
                                            "tool_response": {"content": result.content,
                                                              "is_error": result.is_error}}, tool_name)
        notes = [v.reason for v in verdicts if v.block and v.reason] + [v.context for v in verdicts if v.context]
        return "\n".join(notes) or None

    def stop(self, stop_hook_active: bool) -> str | None:
        verdicts = self.run("Stop", {"stop_hook_active": stop_hook_active})
        return next((v.reason or "Stop hook 要求继续" for v in verdicts if v.block), None)

    def pre_compact(self, trigger: str, instructions: str = "") -> str | None:
        verdicts = self.run("PreCompact", {"trigger": trigger, "custom_instructions": instructions}, trigger)
        return next((v.reason or "PreCompact hook 阻止了压缩" for v in verdicts if v.block), None)
```

结构分两层：

- **`run()` 是通用的**：拼好 stdin 的 JSON，挑出匹配的 hook 逐个执行，把每个命令的退出码和输出翻译成一个 `Verdict`。
- **下面几个方法是给主循环用的入口**，每个负责把多个 `Verdict` 合并成主循环需要的答案。比如 `pre_tool_use` 按 deny > ask > allow 合并，`stop` 只要有一个 hook 阻止就返回理由。

回头看第 2 章的 `agent.py`，这些入口早就被调用了：

| 入口 | 在主循环的哪里 | 结果怎么用 |
| --- | --- | --- |
| `user_prompt_submit` | `query()` 开头 | 被拦下就直接返回 `blocked`；附加的上下文放进 `pending_context` |
| `pre_tool_use` | `_run_one()` 权限检查之前 | deny 直接拒绝；改写的参数重新校验；allow 和 ask 交给权限引擎 |
| `post_tool_use` | `_run_one()` 末尾 | 反馈附在工具结果后面 |
| `stop` | 模型不再调用工具时 | 有理由就追加一条提醒，继续循环 |
| `session_start` | `app.py` 组装时 | 输出放进 `pending_context` |
| `pre_compact` | 第 6 章的压缩器 | 阻止就不压缩 |

`Stop` hook 有个陷阱：如果它总是阻止，模型就永远停不下来。所以主循环传给它一个 `stop_hook_active`：第一次被拦之后就是 `true`，hook 脚本应该检查这个值，避免无限循环。即使脚本写错了，`max_turns` 也会兜底。

## 第 2 步：接进 app.py

在 `minicode/app.py` 里做三处修改。文件开头加两个导入：

```python
import uuid
```

```python
from .hooks import HookRunner
```

把函数最后的 `return Agent(...)` 一行换成：

```python
    hooks = HookRunner(settings["hooks"], cwd, session_id=str(uuid.uuid4()),
                       mode_getter=lambda: permissions.mode) if settings["hooks"] else None   # 第 4 章
    agent = Agent(llm, tools, SYSTEM, cwd=cwd, max_turns=max_turns, permissions=permissions,
                  hooks=hooks, ask_user=ask_user)
    if hooks and (extra := hooks.session_start("startup")):
        agent.pending_context.append(extra)
    return agent
```

改完的完整文件：

```python
"""组装：读配置、建工具、接上各个接缝，得到一个能用的 Agent。对应 Claude Code 启动时做的事。"""
import uuid
from pathlib import Path

from .agent import Agent
from .hooks import HookRunner
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

    hooks = HookRunner(settings["hooks"], cwd, session_id=str(uuid.uuid4()),
                       mode_getter=lambda: permissions.mode) if settings["hooks"] else None   # 第 4 章
    agent = Agent(llm, tools, SYSTEM, cwd=cwd, max_turns=max_turns, permissions=permissions,
                  hooks=hooks, ask_user=ask_user)
    if hooks and (extra := hooks.session_start("startup")):
        agent.pending_context.append(extra)
    return agent
```

会话 ID 暂时随机生成，第 6 章有了真正的会话记录之后会换成会话的 ID。

## 跑起来：三个实用的 hook

hook 脚本放在 `playground/.minicode/hooks/` 下：

```bash
mkdir -p playground/.minicode/hooks
```

**一、不许在 main 分支上推送。** 这种规则用权限规则写不出来，因为它取决于当前在哪个分支：

```bash
cat > playground/.minicode/hooks/no_push_main.py <<'EOF'
import json
import subprocess
import sys

data = json.load(sys.stdin)
command = data["tool_input"].get("command", "")
if "git push" in command:
    branch = subprocess.run(["git", "branch", "--show-current"], capture_output=True, text=True).stdout.strip()
    if branch in ("main", "master"):
        print(f"不允许在 {branch} 分支上直接推送，请先建一个功能分支。", file=sys.stderr)
        sys.exit(2)
EOF
```

**二、改完 Python 文件就检查语法。** `PostToolUse` 拦不住已经发生的修改，但可以立刻告诉模型出了问题：

```bash
cat > playground/.minicode/hooks/check_python.py <<'EOF'
import json
import py_compile
import sys

data = json.load(sys.stdin)
path = data["tool_input"].get("file_path", "")
if path.endswith(".py"):
    try:
        py_compile.compile(path, doraise=True)
    except py_compile.PyCompileError as e:
        print(json.dumps({"decision": "block", "reason": f"刚改的 {path} 有语法错误，请修正：{e.msg}"}))
EOF
```

**三、说“做完了”之前，必须跑过测试。** 翻一遍会话记录，看有没有执行过带 `test` 的命令：

```bash
cat > playground/.minicode/hooks/require_tests.py <<'EOF'
import json
import sys

data = json.load(sys.stdin)
if data["stop_hook_active"]:
    sys.exit(0)          # 已经提醒过一次了，不再阻止，避免死循环
ran_tests = False
if data["transcript_path"]:
    with open(data["transcript_path"], encoding="utf-8") as f:
        for line in f:
            message = json.loads(line).get("message") or {}
            for call in message.get("tool_calls") or []:
                if call["function"]["name"] == "Bash" and "test" in call["function"]["arguments"]:
                    ran_tests = True
if not ran_tests:
    print("结束之前请先运行测试，确认改动没有破坏现有功能。", file=sys.stderr)
    sys.exit(2)
EOF
```

第三个 hook 要读会话记录，第 6 章之后 `transcript_path` 才有值。这一章它每次都会提醒一次，你可以先观察“被拦下之后模型继续工作”的过程。

把三个 hook 加进 `playground/.minicode/settings.json`，和第 3 章的 `permissions` 并列：

```json
{
  "permissions": {"allow": ["Bash(python *)"], "deny": ["Read(.env)"]},
  "hooks": {
    "PreToolUse": [
      {"matcher": "Bash", "hooks": [{"type": "command", "command": "python .minicode/hooks/no_push_main.py"}]}
    ],
    "PostToolUse": [
      {"matcher": "Edit|Write", "hooks": [{"type": "command", "command": "python .minicode/hooks/check_python.py"}]}
    ],
    "Stop": [
      {"hooks": [{"type": "command", "command": "python .minicode/hooks/require_tests.py"}]}
    ]
  }
}
```

hook 命令在 agent 的工作目录里执行，所以路径写相对于 `playground/` 的。启动后让 minicode “新建 bad.py，故意写一个语法错误”，观察 `PostToolUse` 的反馈怎样让它自己改正；再让它做一件小事，观察 `Stop` hook 怎样让它在结束前补跑测试。

## 离线测试

新建 `tests/test_ch04.py`：

```python
import sys
import textwrap

from minicode.agent import Agent
from minicode.hooks import HookRunner, matcher_hits
from minicode.permissions import PermissionEngine
from minicode.testing import FakeLLM, call
from minicode.tools import core_tools


def hook_script(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return f'"{sys.executable}" "{path}"'


def test_matcher():
    assert matcher_hits("Edit|Write", "Write") and not matcher_hits("Edit|Write", "Bash")
    assert matcher_hits("mcp__.*", "mcp__labnotes__lookup_sample")


def test_pre_tool_use_blocks_with_exit_2(tmp_path):
    guard = hook_script(tmp_path, "guard.py", """
        import json, sys
        if "rm " in json.load(sys.stdin)["tool_input"]["command"]:
            print("禁止删除文件", file=sys.stderr)
            sys.exit(2)
    """)
    hooks = HookRunner({"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": guard}]}]},
                       tmp_path)
    llm = FakeLLM([call("Bash", command="rm -rf build"), "好的"])
    agent = Agent(llm, core_tools(), "s", cwd=tmp_path, hooks=hooks,
                  permissions=PermissionEngine(mode="bypassPermissions", workdirs=[tmp_path]))
    events = list(agent.query("清理一下"))
    assert events[1]["is_error"] and "禁止删除文件" in events[1]["content"]


def test_stop_hook_makes_agent_continue(tmp_path):
    stop = hook_script(tmp_path, "stop.py", """
        import json, sys
        if not json.load(sys.stdin)["stop_hook_active"]:
            print("还没运行测试", file=sys.stderr)
            sys.exit(2)
    """)
    hooks = HookRunner({"Stop": [{"hooks": [{"type": "command", "command": stop}]}]}, tmp_path)
    llm = FakeLLM(["做完了", "测试也跑过了，做完了"])
    events = list(Agent(llm, [], "s", cwd=tmp_path, hooks=hooks).query("x"))
    assert events[-1]["text"] == "测试也跑过了，做完了"
    assert "还没运行测试" in llm.requests[1]["messages"][-1]["content"]
```

```bash
uv run python -m pytest tests -q
```

## 安全：hook 是以你的身份运行的代码

hook 命令拥有和你一样的权限，能读你的文件、用你的网络。项目级的 `.minicode/settings.json` 是跟着仓库走的，**克隆一个陌生仓库，就等于可能运行了别人写的 hook**。Claude Code 对此的做法是：第一次在某个目录工作时要求用户确认信任这个目录，项目配置里的 allow 规则也要在信任之后才生效。minicode 没有实现信任机制，所以不要在不信任的仓库里运行它（进阶练习 3）。

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| 事件 | 三十多种，包括 `PermissionRequest`、`SubagentStop`、`SessionEnd`、`Notification` 等 | 六种 |
| hook 类型 | 命令，还有 HTTP、MCP 工具、提示词（让模型判断）、agent 等类型 | 只有命令 |
| 默认超时 | 命令 hook 600 秒 | 60 秒 |
| 条件过滤 | 除了 `matcher`，还可以用 `if` 字段写权限规则语法的条件 | 只有 `matcher` |
| 其他输出字段 | `continue: false` 停止整个处理、`systemMessage`、`updatedToolOutput` 改写工具结果等 | 不支持 |
| 信任 | 项目配置要在信任目录后才完全生效 | 没有 |

## 自检清单

- [ ] 能说出退出码 0、2、其他各代表什么，以及为什么“阻止”用 2。
- [ ] 能解释为什么 `PostToolUse` 拦不住操作，它的反馈有什么用。
- [ ] 能写一个 `PreToolUse` hook，用 `updatedInput` 给所有 `pytest` 命令加上 `-q`。
- [ ] 知道 `stop_hook_active` 是用来防什么的。
- [ ] 能分辨一个需求该用权限规则还是 hook（提示：要不要看“现在的状态”）。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| hook 没有任何效果 | `matcher` 没匹配上；或者 JSON 打印到了 stderr | 先用 `"matcher": "*"` 确认 hook 被调用；决定写在 stdout |
| 想拦截却拦不住 | 脚本用 `sys.exit(1)` 退出 | 阻止要用退出码 2 |
| agent 一直停不下来 | `Stop` hook 总是返回 2 | 检查 `stop_hook_active` |
| 改写参数后工具报“缺少必填参数” | `updatedInput` 只写了改动的字段 | 它会整个替换参数，要带上所有字段 |
| hook 很慢 | 每次工具调用都要启动一个 Python 进程 | `matcher` 写精确；重的检查放到 `Stop` 里一次做完 |

## 进阶练习

1. 写一个 `UserPromptSubmit` hook：用户的输入里像是包含密钥（比如 `sk-` 开头的长串）时，拦下并提醒。
2. 写一个 `PreCompact` hook，把压缩前的完整会话记录复制一份存档。
3. 实现简单的信任机制：第一次在某个目录运行时，如果有项目级的 hooks，列出它们并要求用户确认；确认结果记在 `~/.minicode/trusted.json`。
4. 支持 `"type": "prompt"` 的 hook：不运行命令，而是把事件发给模型，让模型判断要不要阻止。想一想它适合什么场景，又有什么风险。

## 资料来源

- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks)
- [Claude Code: Configure permissions](https://code.claude.com/docs/en/permissions)：hook 和权限规则的关系
- [Agent SDK: Configure permissions](https://code.claude.com/docs/en/agent-sdk/permissions)
