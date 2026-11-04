# 第 7 章：规划，把计划从模型脑子里搬到 harness 手里

## 导读

一个十几步的任务，模型做到第八步时，常常已经忘了第三步说过要回头补什么；或者一上来就开始改代码，改到一半才发现方向错了。人解决这类问题的办法很朴素：**先列清单，先想清楚再动手**。Claude Code 把这两个办法做成了两个机制：

- **待办清单**：模型用一个工具维护任务清单。清单是 harness 持有的状态，界面能显示它，压缩后能恢复它，模型忘了更新时 harness 会提醒它。
- **plan 模式**：一个只读的权限模式。模型只能调研、写计划，计划经用户批准后才能开始修改。

学完你会有：

- `minicode/tools/planning.py`：`TodoWrite` 工具和提醒、`ExitPlanMode` 工具和 plan 模式提醒。
- 能回答：为什么一个“不做任何事”的工具能提高长任务的完成率？plan 模式靠什么保证模型真的不改文件？

## Claude Code 是怎么做的

### 待办清单

Anthropic 的 context engineering 文章把待办清单归为一种“结构化笔记”：

> Structured note-taking, or agentic memory, is a technique where the agent regularly writes notes persisted to memory outside of the context window.

文章举的例子就是 “Claude Code creating a to-do list”。清单写在上下文窗口之外，所以即使对话被压缩，计划也不会丢。

Claude Code 早期用的是 `TodoWrite` 工具：模型每次传入完整的清单，每一项有内容和状态（pending、in_progress、completed），同一时间只有一项是 in_progress。在新版本里，[工具列表](https://code.claude.com/docs/en/tools-reference)显示 `TodoWrite` 默认被一组任务工具（`TaskCreate`、`TaskUpdate`、`TaskList` 等）取代，思路相同，只是把“整个清单一次写入”拆成了按条目增删改。minicode 用更简单的 `TodoWrite` 形式。

使用 Claude Code 时你还能观察到一个细节：如果模型有一阵子没用清单工具，harness 会在上下文里插入一条提醒，让它检查清单是否需要更新。minicode 照这个思路实现，并在提醒里加了一句“不要向用户提起这条提醒”，免得模型把内部提示念给用户听。

### plan 模式

权限文档对 plan 模式的描述是：Claude 读文件、运行只读的命令，但不修改源文件。Claude Code 里有两个配套工具：`EnterPlanMode` 进入 plan 模式，`ExitPlanMode` 把写好的计划交给用户批准并退出 plan 模式。`ExitPlanMode` 在工具列表里标为需要权限，因为“批准计划”本身就是一次用户决策。

plan 模式的保证不是靠“请模型不要改文件”，而是靠**权限系统**：在这个模式下，改文件的工具根本不会被放行。提示词只是让模型知道自己处在什么模式、该做什么。

## 第 1 步：planning.py

新建 `minicode/tools/planning.py`：

```python
"""规划工具：TodoWrite（待办清单）和 ExitPlanMode（提交计划，请用户批准）。

待办清单不改变外部世界，但它把“计划”从模型脑子里搬到了 harness 手里：
harness 能显示它、在压缩后恢复它、在模型忘了更新时提醒它。
"""
from ..tool import Tool, ToolError

STATUSES = ["pending", "in_progress", "completed"]
NUDGE_AFTER = 6          # 连续多少次模型回复没有更新清单，就提醒一次


class TodoWrite(Tool):
    name = "TodoWrite"
    description = ("创建并更新当前任务的待办清单。三步以上的任务，开始前先列清单；"
                   "开始做某一项时把它标为 in_progress（同一时间只能有一项），做完立刻标为 completed。"
                   "每次调用都传入完整的清单，它会整个替换旧清单。简单的一两步任务不需要清单。")
    parameters = {"type": "object", "properties": {
        "todos": {"type": "array", "description": "完整的待办清单",
                  "items": {"type": "object", "properties": {
                      "content": {"type": "string", "description": "这一项要做什么"},
                      "status": {"type": "string", "enum": STATUSES}},
                      "required": ["content", "status"]}}},
        "required": ["todos"]}
    read_only = True     # 只改 agent 自己的状态，不碰外部世界

    def call(self, args, ctx):
        todos = args["todos"]
        for t in todos:
            if not isinstance(t, dict) or t.get("status") not in STATUSES or not t.get("content"):
                raise ToolError(f"每一项都需要 content 和 status（{'/'.join(STATUSES)}），有问题的是：{t}")
        if sum(t["status"] == "in_progress" for t in todos) > 1:
            raise ToolError("同一时间只能有一项是 in_progress。")
        ctx.todos = [{"content": t["content"], "status": t["status"]} for t in todos]
        return "待办清单已更新：\n" + render_todos(ctx.todos)


def render_todos(todos: list[dict]) -> str:
    marks = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}
    return "\n".join(f"{marks[t['status']]} {t['content']}" for t in todos) or "（空）"


def todo_reminder(agent) -> str | None:
    """清单里还有没做完的事，而模型已经好几轮没更新它了：提醒一下。"""
    todos = agent.ctx.todos
    if not todos or all(t["status"] == "completed" for t in todos):
        return None
    since = 0
    for m in reversed(agent.messages):
        if m["role"] != "assistant":
            continue
        if any(c["function"]["name"] == "TodoWrite" for c in m.get("tool_calls") or []):
            break
        since += 1
    if since < NUDGE_AFTER:
        return None
    return ("待办清单已经有一段时间没有更新了。如果进度有变化，用 TodoWrite 更新它；"
            "不要向用户提起这条提醒。当前清单：\n" + render_todos(todos))


# ---------- plan 模式 ----------
class ExitPlanMode(Tool):
    name = "ExitPlanMode"
    description = ("在 plan 模式下调研完成、写好实施计划后调用：把计划交给用户批准。"
                   "批准后才能开始修改文件。只用于需要写代码的任务，纯调研问题直接回答即可。")
    parameters = {"type": "object", "properties": {
        "plan": {"type": "string", "description": "实施计划，用 Markdown 写，简洁具体"}},
        "required": ["plan"]}
    read_only = True     # 它本身不改东西；是否批准由它自己向用户询问

    def call(self, args, ctx):
        agent = ctx.agent
        if agent.permissions is None or agent.permissions.mode != "plan":
            raise ToolError("当前不在 plan 模式，不需要提交计划。")
        answer = agent.ask_user(self.name, args, "批准这个计划并开始执行吗？")
        if answer == "no":
            return "用户没有批准这个计划。继续留在 plan 模式，根据用户的意见调整计划。"
        agent.permissions.mode = "acceptEdits" if answer == "always" else "default"
        return f"用户批准了计划，已切换到 {agent.permissions.mode} 模式。现在开始按计划执行。"


def plan_mode_reminder(agent) -> str | None:
    if agent.permissions is None or agent.permissions.mode != "plan":
        return None
    return ("现在是 plan 模式：只做调研，不要修改任何文件、不要执行有副作用的命令。"
            "调研清楚后写出实施计划，用 ExitPlanMode 提交给用户批准。")
```

几个要点：

- **`TodoWrite` 标成只读。** 它只改 agent 自己的状态，不碰外部世界，所以不需要权限、plan 模式下也能用（在 plan 模式里列清单正合适）。
- **校验在工具里做。** 状态拼错、两项同时进行中，都返回 `ToolError`，模型下一轮会改正。
- **`todo_reminder` 往回数 assistant 消息。** 从最近的一条往前数，数到上一次调用 `TodoWrite` 为止。连续 6 次回复都没更新、清单里还有没做完的项，就提醒一次。提醒通过第 5 章的 reminders 机制临时附加，不写进历史。
- **`ExitPlanMode` 自己向用户提问。** 它借用 agent 的 `ask_user`：批准就切到 default 模式，选“总是允许”就切到 acceptEdits，拒绝就留在 plan 模式。
- **`plan_mode_reminder` 只在 plan 模式下返回内容**，每一轮都提醒模型当前的模式，模式一切换，提醒就自动消失。

第 6 章的压缩器会把 `ctx.todos` 附在摘要后面，所以清单能熬过压缩。

## 第 2 步：接进 app.py

在 `minicode/app.py` 里加三处。导入：

```python
from .tools.planning import ExitPlanMode, TodoWrite, plan_mode_reminder, todo_reminder
```

工具列表：

```python
    tools = core_tools() + [WebFetch()]                                       # 第 2、5 章
    tools += [TodoWrite(), ExitPlanMode()]                                    # 第 7 章
```

创建 `Agent` 时，把最后的 `ask_user=ask_user)` 换成带上 reminders：

```python
                  reminders=[plan_mode_reminder, todo_reminder], ask_user=ask_user)   # 第 7 章
```

改完的完整文件：

```python
"""组装：读配置、建工具、接上各个接缝，得到一个能用的 Agent。对应 Claude Code 启动时做的事。"""
import os
from pathlib import Path

from .agent import Agent
from .compact import Compactor
from .context import auto_memory_file, build_system_prompt, memory_context
from .hooks import HookRunner
from .llm import LLM
from .permissions import PermissionEngine, parse_rule
from .session import Session
from .settings import CONFIG_DIR, load_settings
from .tools import core_tools
from .tools.planning import ExitPlanMode, TodoWrite, plan_mode_reminder, todo_reminder
from .tools.web import WebFetch


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
        workdirs=[cwd, auto_memory_file(cwd, home).parent, *perms.get("additionalDirectories", [])],
        persist_to=cwd / CONFIG_DIR / "settings.local.json")

    tools = core_tools() + [WebFetch()]                                       # 第 2、5 章
    tools += [TodoWrite(), ExitPlanMode()]                                    # 第 7 章
    removed = {parse_rule(r)[0] for r in permissions.rules["deny"] if parse_rule(r)[1] is None}
    tools = [t for t in tools if t.name not in removed]       # deny 里只写工具名 = 让模型根本看不到这个工具

    if resume or continue_session:                                            # 第 6 章
        session, history = Session.resume(cwd, resume, home)
    else:
        session, history = Session(cwd, home=home), []
    hooks = HookRunner(settings["hooks"], cwd, session.id, str(session.path),
                       lambda: permissions.mode) if settings["hooks"] else None   # 第 4 章
    memory = memory_context(cwd, home)                                        # 第 5 章
    agent = Agent(llm, tools, build_system_prompt(cwd, llm.model), cwd=cwd,   # 第 5 章
                  max_turns=max_turns, permissions=permissions, hooks=hooks, session=session,
                  compactor=Compactor(llm, int(os.getenv("MINICODE_CONTEXT_WINDOW", 128_000)), memory=memory),
                  reminders=[plan_mode_reminder, todo_reminder], ask_user=ask_user)   # 第 7 章

    if history:
        agent.messages = [agent.messages[0], *history[1:]]    # 换成最新的系统提示词
        agent.repair()                                        # 上次如果停在工具执行中间，补上结果
    else:
        session.append(agent.messages[0])
        agent.pending_context.append(memory)
    if hooks and (extra := hooks.session_start("resume" if history else "startup")):
        agent.pending_context.append(extra)
    return agent
```

## 跑起来

**待办清单。** 给一个多步任务：

```text
> 新建一个 units.py，实现 OD600 读数格式化（保留两位小数）和 µg/mL 与 mg/L 的换算，再写 test_units.py 测试它们，最后运行测试
```

你应该看到模型先调用 `TodoWrite` 列出几项，然后每开始一项、完成一项都更新一次。随时输入 `/todos` 查看当前清单。

**plan 模式。** 用 plan 模式启动，给一个需要改代码的任务：

```bash
uv run python -m minicode --cwd playground --permission-mode plan
```

```text
> 给 stats.py 加一个 median 函数，并补上测试
```

模型会先读代码、调研，然后调用 `ExitPlanMode`。你会看到它写的计划，回答 `y` 批准后它才开始修改。可以试试回答 `n`，然后告诉它你希望计划怎么改。

在交互模式里也可以随时用 `/mode plan` 切换。

## 离线测试

新建 `tests/test_ch07.py`：

```python
from minicode.agent import Agent
from minicode.permissions import PermissionEngine
from minicode.testing import FakeLLM, call
from minicode.tools import core_tools
from minicode.tools.planning import ExitPlanMode, TodoWrite, plan_mode_reminder, todo_reminder


def test_todo_nudge(tmp_path):
    todos = [{"content": "改代码", "status": "in_progress"}, {"content": "跑测试", "status": "pending"}]
    llm = FakeLLM([call("TodoWrite", todos=todos)] + [call("Glob", pattern="*")] * 7 + ["完成"])
    agent = Agent(llm, core_tools() + [TodoWrite()], "s", cwd=tmp_path, reminders=[todo_reminder])
    list(agent.query("x"))
    assert agent.ctx.todos[1]["content"] == "跑测试"
    nudged = [i for i, r in enumerate(llm.requests) if "待办清单已经有一段时间" in r["messages"][-1]["content"]]
    assert nudged and nudged[0] >= 6


def test_plan_mode_then_approve(tmp_path):
    permissions = PermissionEngine(mode="plan", workdirs=[tmp_path])
    llm = FakeLLM([call("Write", file_path="a.txt", content="x"),
                   call("ExitPlanMode", plan="1. 新建 a.txt"),
                   call("Write", file_path="a.txt", content="x"), "完成"])
    agent = Agent(llm, core_tools() + [ExitPlanMode()], "s", cwd=tmp_path, permissions=permissions,
                  reminders=[plan_mode_reminder], ask_user=lambda tool, args, reason: "yes")
    results = [e for e in agent.query("x") if e["type"] == "tool_result"]
    assert "plan 模式下只能调研" in results[0]["content"]        # 批准前写不了
    assert permissions.mode == "default" and (tmp_path / "a.txt").exists()
    assert "plan 模式" in llm.requests[0]["messages"][-1]["content"]
```

```bash
uv run python -m pytest tests -q
```

## 规划的边界

清单和计划解决的是“忘了”和“没想清楚”，解决不了“想错了”。几条经验：

- **简单任务不要列清单。** 一两步就能完成的事，列清单只是浪费一轮请求。`TodoWrite` 的说明里写了“三步以上”。
- **计划要能检查。** “优化性能”不是计划，“把 `load_all()` 改成分批读取，每批 1000 条，用现有的 `test_load.py` 验证”才是。
- **plan 模式的价值在于给你一个插手的时机。** 模型的计划可能遗漏约束，这时修改计划比事后回滚便宜得多。

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| 任务清单 | 新版用 TaskCreate / TaskUpdate / TaskList 等工具，按条目操作 | `TodoWrite`，每次写入整个清单 |
| 进入 plan 模式 | 用户切换，或者模型调用 `EnterPlanMode` | 只能由用户切换（`--permission-mode plan` 或 `/mode plan`） |
| plan 模式下的修改 | 文件修改不会被自动批准 | 非只读工具一律拒绝 |
| plan 模式下的调研 | 内置的 Plan 子 agent 负责调研 | 主 agent 自己调研 |
| 计划的保存 | 计划会单独显示给用户审阅 | 打印在终端里，并留在会话记录中 |

## 自检清单

- [ ] 能解释“结构化笔记”为什么能熬过压缩。
- [ ] 能说出 `todo_reminder` 在什么条件下触发，为什么不写进历史。
- [ ] 能说出 plan 模式的“只读”是由哪段代码保证的。
- [ ] 能举一个你批准前修改了计划、避免了返工的例子。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 模型每个小问题都列清单 | 说明里没写清什么时候不用 | 在说明里强调“简单任务不需要” |
| 清单做完了，模型不标 completed | 模型不重视清单 | 提醒机制会推它；也可以在 Stop hook 里检查清单是否还有未完成项 |
| plan 模式下模型反复尝试写文件 | 没看到或没理解提醒 | 拒绝信息里已经写明“用 ExitPlanMode 提交”；检查 reminders 是否接上 |
| 批准计划后模型马上做了计划外的事 | 计划不够具体 | 拒绝那个计划，要求写到文件和函数级别 |

## 进阶练习

1. 写一个 `Stop` hook：如果待办清单里还有没完成的项，不让模型结束。提示：hook 拿不到 `ctx.todos`，可以从会话记录里找最后一次 `TodoWrite` 调用的参数。
2. 实现 `EnterPlanMode` 工具：模型遇到复杂任务时可以主动进入 plan 模式。想一想：模型主动进入只读模式有风险吗？主动退出呢？
3. 把清单改成按条目操作的三个工具（创建、更新、列出），比较两种设计在长任务里的 token 消耗。

## 资料来源

- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：结构化笔记
- [Claude Code 工具列表](https://code.claude.com/docs/en/tools-reference)：TodoWrite、Task 系列工具、EnterPlanMode、ExitPlanMode
- [Claude Code: Configure permissions](https://code.claude.com/docs/en/permissions)：plan 模式
