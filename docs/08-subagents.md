# 第 8 章：子 agent，多 agent 是协调问题，不是魔法

## 导读

“多个 agent 协作”听起来很强大：一个负责调研，一个负责写，一个负责审查。但真正做过就会发现，多数时候它们只是在互相传话、重复劳动、把 token 花掉好几倍。

Claude Code 对多 agent 的用法很克制：主 agent 通过一个 **Agent 工具**派出子 agent，子 agent 在**自己独立的上下文**里完成一件事，只把一份简短的报告带回来。它的核心价值不是“多个脑子”，而是**上下文隔离**：子 agent 读了几十个文件、跑了一堆命令，主 agent 的上下文里只多了几百个字。

学完你会有：

- `minicode/subagents.py`：子 agent 的定义格式（Markdown + frontmatter）和内置的两种子 agent。
- `minicode/tools/task.py`：Agent 工具，支持并行派出多个子 agent。
- 一个“写 → 审查”的协作例子，以及判断“该不该用多 agent”的标准。

## Claude Code 是怎么做的

以下内容来自 Claude Code 的[子 agent 文档](https://code.claude.com/docs/en/sub-agents)。

### 定义一个子 agent

子 agent 是一个 Markdown 文件，放在 `.claude/agents/`（项目）或 `~/.claude/agents/`（用户）下：

```markdown
---
name: reviewer
description: 代码审查专家。改完代码后主动使用。
tools: Read, Grep, Glob
---
你是一名严格的代码审查员……
```

frontmatter 里只有 `name` 和 `description` 是必填的。`description` 写的是“什么时候应该派它”，主 agent 靠它决定要不要委派；`tools` 不写就继承全部工具；正文就是子 agent 的系统提示词。文档特别说明，子 agent 收到的是**这段提示词加上基本的环境信息**，而不是 Claude Code 完整的系统提示词。

还有很多可选字段，比如 `model`、`permissionMode`、`maxTurns`、`skills`、`mcpServers`、`hooks` 等。

### 内置的子 agent

| 名字 | 用途 |
| --- | --- |
| Explore | 快速、只读的代码库搜索和分析，Write 和 Edit 被禁用 |
| Plan | plan 模式下负责调研 |
| general-purpose | 能力完整，适合既要调研又要动手的多步任务 |

调用子 agent 的工具叫 **Agent**（旧名字 Task 仍然兼容）。权限规则里也用这个名字，比如 `Agent(Explore)`。

### 上下文隔离

> Each subagent runs in its own context window with a custom system prompt, specific tool access, and independent permissions.

子 agent 看不到主对话的历史。context engineering 文章给了一个量级：子 agent 可能用掉几万甚至更多 token 去探索，但只返回一份“often 1,000-2,000 tokens”的精炼总结。

### 什么时候不该用多 agent

Anthropic 在介绍他们的[多 agent 研究系统](https://www.anthropic.com/engineering/multi-agent-research-system)时给了两组数字和一个判断：

- agent 通常比普通聊天多用约 4 倍 token，多 agent 系统多用约 15 倍。
- 多 agent 擅长“可以大量并行、信息量超过单个上下文、需要对接很多复杂工具”的高价值任务。
- 但是：“some domains that require all agents to share the same context or involve many dependencies between agents are not a good fit for multi-agent systems today. For instance, most coding tasks involve fewer truly parallelizable tasks than research”。

所以写代码这件事，主力仍然是一个 agent；子 agent 适合的是**能独立完成、只需要交回结论**的支线任务。

## 第 1 步：子 agent 的定义

新建 `minicode/subagents.py`：

```python
"""子 agent 的定义：一个 Markdown 文件 = frontmatter（名字、描述、工具）+ 正文（系统提示词）。

和 Claude Code 的 .claude/agents/*.md 格式一样，放在 .minicode/agents/ 或 ~/.minicode/agents/ 下。
"""
from dataclasses import dataclass
from pathlib import Path

from .settings import CONFIG_DIR


@dataclass
class AgentDef:
    name: str
    description: str
    prompt: str
    tools: list[str] | None = None     # None = 继承父 agent 的全部工具（Agent 工具本身除外）
    max_turns: int = 30


EXPLORE_PROMPT = """你是一个只读的代码库探索助手。用 Glob、Grep、Read 快速找到答案，不要修改任何东西。
完成后给出简洁的报告：结论、关键文件路径和行号、你不确定的地方。报告就是你唯一的输出，主 agent 看不到你的中间过程。"""

GENERAL_PROMPT = """你是一个子 agent，负责独立完成主 agent 交给你的一项任务。你看不到主 agent 和用户的对话，
所有背景都在任务描述里。完成后给出简洁的报告：做了什么、结果如何、改了哪些文件、还有什么问题。"""

BUILTIN = [
    AgentDef("explore", "只读探索代码库：查找文件、搜索代码、回答“X 在哪里、怎么实现的”。需要读很多文件时用它，"
             "可以避免大量文件内容挤占主对话的上下文。", EXPLORE_PROMPT, ["Read", "Glob", "Grep"]),
    AgentDef("general-purpose", "通用子 agent：独立完成一项复杂的多步任务（调研、修改、验证都可以）。",
             GENERAL_PROMPT),
]


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析开头 --- 之间的 key: value。够用就好，不支持完整的 YAML。"""
    if not text.startswith("---"):
        return {}, text
    head, sep, body = text[3:].partition("\n---")
    if not sep:
        return {}, text
    meta = {}
    for line in head.strip().splitlines():
        key, colon, value = line.partition(":")
        if colon:
            meta[key.strip()] = value.strip().strip("'\"")
    return meta, body.lstrip("-").lstrip("\n")


def load_agent_defs(cwd: Path, home: Path | None = None) -> dict[str, AgentDef]:
    defs = {d.name: d for d in BUILTIN}
    for directory in [(home or Path.home()) / CONFIG_DIR / "agents", cwd / CONFIG_DIR / "agents"]:
        for f in sorted(directory.glob("*.md")) if directory.is_dir() else []:
            meta, body = parse_frontmatter(f.read_text(encoding="utf-8"))
            if not meta.get("name") or not meta.get("description"):
                continue                          # name 和 description 是必填的
            tools = [t.strip() for t in meta["tools"].split(",") if t.strip()] if meta.get("tools") else None
            defs[meta["name"]] = AgentDef(meta["name"], meta["description"], body.strip(), tools,
                                          int(meta.get("maxTurns", 30)))
    return defs
```

`parse_frontmatter` 只解析最简单的 `key: value`，够用就好；第 9 章的 skill 也用它。项目级的定义会覆盖同名的内置定义。

## 第 2 步：Agent 工具

新建 `minicode/tools/task.py`：

```python
"""Agent 工具：派一个子 agent 去独立完成任务，只把最终报告带回来。

子 agent 的价值在于上下文隔离：它可能读了几十个文件、用掉几万 token，
主 agent 的上下文里只多了一份一两千 token 的报告。
"""
from ..context import build_system_prompt
from ..tool import Tool, ToolError, ToolResult


class AgentTool(Tool):
    name = "Agent"
    read_only = True     # 调用本身没有副作用；子 agent 的每次工具调用各自过权限检查

    def __init__(self, defs: dict):
        self.defs = defs
        listing = "\n".join(f"- {d.name}：{d.description}" for d in defs.values())
        self.description = ("启动一个子 agent 独立完成一项任务，它完成后只返回一份报告。"
                            "子 agent 看不到当前对话，所以 prompt 里要写清背景、目标和希望报告包含什么。"
                            "互相独立的几项任务可以在一次回复里同时启动多个子 agent。"
                            f"可用的类型：\n{listing}")
        self.parameters = {"type": "object", "properties": {
            "description": {"type": "string", "description": "3 到 10 个字的任务标题"},
            "prompt": {"type": "string", "description": "交给子 agent 的完整任务说明"},
            "subagent_type": {"type": "string", "enum": list(defs)}},
            "required": ["description", "prompt", "subagent_type"]}

    def call(self, args, ctx):
        from ..agent import Agent  # 放在这里导入，避免循环导入
        parent = ctx.agent
        d = self.defs[args["subagent_type"]]
        tools = [t for t in parent.tools.values()
                 if t.name != self.name and (d.tools is None or t.name in d.tools)]   # 不允许再派子 agent
        system = build_system_prompt(ctx.cwd, getattr(parent.llm, "model", ""), base=d.prompt)
        child = Agent(parent.llm, tools, system, cwd=ctx.cwd, max_turns=d.max_turns, price=parent.price,
                      permissions=parent.permissions, hooks=parent.hooks, ask_user=parent.ask_user,
                      reminders=parent.reminders, parent=parent)   # 用量实时计入父 agent，共用预算
        final = None
        for event in child.query(args["prompt"]):
            if event["type"] == "result":
                final = event
        if final is None:
            raise ToolError("子 agent 没有返回结果")
        stats = f"[子 agent {d.name}：{final['num_turns']} 轮，{sum(child.usage.values())} tokens]"
        if final["subtype"] != "success":
            return ToolResult(f"子 agent 没有完成任务（{final['subtype']}）：{final['text']}\n{stats}", True)
        return f"{final['text']}\n\n{stats}"
```

读 `call()` 时注意哪些东西子 agent 和主 agent **共享**，哪些**不共享**：

| | 共享吗 | 为什么 |
| --- | --- | --- |
| 模型客户端、价格、预算 | 共享 | 子 agent 的每次请求都实时计入主 agent 的用量；预算按最上层的 agent 算，子 agent 每一轮开始前都检查，并行的多个子 agent 也不会各花各的 |
| 权限引擎、hooks、`ask_user` | 共享 | 子 agent 的每次工具调用都要过同样的检查，不能借子 agent 绕过权限 |
| 消息历史 | **不共享** | 子 agent 从一条全新的消息开始，只知道 prompt 里写的内容 |
| `ToolContext`（读过哪些文件、待办清单） | **不共享** | 子 agent 读过的文件不算主 agent 读过，主 agent 想改仍然要自己先 Read |
| 会话记录 | 不记录 | 子 agent 的过程不进主会话（见进阶练习 2） |

还有两个限制：

- **子 agent 不能再派子 agent。** 工具列表里去掉了 Agent 工具本身，防止无限递归。Claude Code 的嵌套规则请以官方文档为准。
- **Agent 工具标成只读。** 调用它本身没有副作用；子 agent 要做的每件事各自过权限检查。标成只读的另一个效果是：模型在一次回复里派出多个子 agent 时，它们会**并行**运行（第 2 章的 `_run_tools`），多个子 agent 同时要求确认时，`ask_user` 外面的锁保证一次只问一个。

## 第 3 步：接进 app.py

在 `minicode/app.py` 里加两个导入：

```python
from .subagents import load_agent_defs
```

```python
from .tools.task import AgentTool
```

在待办工具那一行下面加：

```python
    tools.append(AgentTool(load_agent_defs(cwd, home)))                       # 第 8 章
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
from .subagents import load_agent_defs
from .tools import core_tools
from .tools.planning import ExitPlanMode, TodoWrite, plan_mode_reminder, todo_reminder
from .tools.task import AgentTool
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
    tools.append(AgentTool(load_agent_defs(cwd, home)))                       # 第 8 章
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

**用内置的 explore 子 agent 做调研。** 在一个文件比较多的目录里试（比如把 `--cwd` 指向你的 minicode 源码，只读不写）：

```bash
uv run python -m minicode --cwd . --permission-mode plan
```

```text
> 用 explore 子 agent 分别调查：1) 权限检查是在哪些文件里实现的；2) 会话记录是怎么写入磁盘的。两个调查同时进行。
```

你会看到一次回复里出现两个 `● Agent(...)`，它们并行运行，各自返回一份带文件路径和行号的报告。输入 `/context` 对比：主对话的上下文只增加了两份报告的大小。

**写一个审查员，组成“写 → 审查”的流程。**

```bash
mkdir -p playground/.minicode/agents
```

```bash
cat > playground/.minicode/agents/reviewer.md <<'EOF'
---
name: reviewer
description: 代码审查员。主 agent 写完或改完代码后，派它独立审查，不要自己审查自己的代码。
tools: Read, Grep, Glob, Bash
---
你是一名严格的代码审查员。你会收到一份改动说明，请：
1. 读相关文件，必要时运行测试；
2. 只报告真实存在的问题，按严重程度排序，每条写清文件路径、行号、问题和修改建议；
3. 没有问题就明确说“没有发现问题”，不要为了显得认真而凑问题。
你不能修改任何文件。最后的报告就是你唯一的输出。
EOF
```

```text
> 给 stats.py 加一个 median 函数和对应的测试，写完后派 reviewer 审查，根据审查意见修改，直到审查通过
```

注意 reviewer 的 `tools` 里有 Bash（为了跑测试），但它的系统提示词说不能改文件。提示词不是安全边界：如果你真的不想让它改，把 Bash 从 `tools` 里去掉，或者用权限规则限制它能跑的命令。

## 多 agent 协作的几条规矩

如果你想搭更复杂的多 agent 流程（比如“调研 → 写作 → 审查 → 修改”这样的流水线），下面几条比框架选择重要得多：

1. **先问能不能用一个 agent 做。** 任务之间依赖紧密、需要共享大量上下文时，拆开只会增加传话的损耗。
2. **每个 agent 的输入输出要写成契约。** 报告里必须有什么（结论、证据、文件路径）、长度上限是多少，都写进系统提示词。上游的输出不可靠，下游再聪明也没用。
3. **停止条件写在代码里，而不是靠 agent 自己判断。** “审查通过或者最多三轮”这样的循环上限，放在 harness 里控制。
4. **能用代码检查的，就不要让 agent 检查。** 测试通没通过、文件有没有改，跑个命令就知道了，不要再派一个 agent 去“判断”。
5. **记录每个 agent 用了多少 token。** 多 agent 的成本是成倍增加的，你要能看到钱花在哪里。

## 离线测试

新建 `tests/test_ch08.py`：

```python
from minicode.agent import Agent
from minicode.subagents import load_agent_defs
from minicode.testing import FakeLLM, call
from minicode.tools import core_tools
from minicode.tools.task import AgentTool


def test_subagent_has_own_context(tmp_path):
    (tmp_path / ".minicode/agents").mkdir(parents=True)
    (tmp_path / ".minicode/agents/reviewer.md").write_text(
        "---\nname: reviewer\ndescription: 审查代码\ntools: Read, Grep\n---\n你是严格的代码审查员。\n")
    (tmp_path / "a.py").write_text("print(1)\n")
    llm = FakeLLM([call("Agent", description="审查", prompt="审查 a.py", subagent_type="reviewer"),
                   call("Read", file_path="a.py"),          # 这是子 agent 的第 1 轮
                   "a.py 没有问题",                          # 子 agent 的报告
                   "审查结论：没有问题"])                    # 主 agent 的最终回答
    agent = Agent(llm, core_tools() + [AgentTool(load_agent_defs(tmp_path))], "主 agent", cwd=tmp_path)
    events = list(agent.query("审查一下代码"))
    child = llm.requests[1]
    assert child["messages"][0]["content"].startswith("你是严格的代码审查员。")
    assert child["messages"][1]["content"] == "审查 a.py"                  # 看不到主对话
    assert [t["function"]["name"] for t in child["tools"]] == ["Read", "Grep"]
    assert "a.py 没有问题" in events[1]["content"]
    assert "print(1)" not in str(agent.messages)                           # 文件内容没进主上下文


def test_subagents_share_the_budget(tmp_path):
    llm = FakeLLM([call("Agent", description="查", prompt="查文件", subagent_type="explore")]
                  + [call("Glob", pattern="*")] * 10 + ["报告", "完成"])
    agent = Agent(llm, core_tools() + [AgentTool(load_agent_defs(tmp_path))], "s", cwd=tmp_path,
                  max_budget_usd=0.0003, price=(0.1, 0.1))
    events = list(agent.query("x"))
    assert events[-1]["subtype"] == "error_max_budget_usd"
    assert agent.cost_usd() < 0.0005                               # 子 agent 没有无视预算一直跑下去
```

```bash
uv run python -m pytest tests -q
```

这个测试的剧本里，第 2、3 条回复是“子 agent 的”，第 1、4 条是“主 agent 的”：子 agent 在 Agent 工具执行期间同步运行，所以它们共用一份剧本，按时间顺序排列。测试检查了上下文隔离的三个方面：子 agent 的系统提示词、它看不到主对话、它读过的文件内容没有进入主 agent 的上下文。

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| 嵌套 | 有专门的规则和配置，见官方文档 | 不允许 |
| 后台运行 | 子 agent 可以在后台运行，主 agent 继续干别的 | 只能前台；多个可以并行，但主 agent 要等它们都结束 |
| 权限模式 | 可以在定义里指定 `permissionMode`；主会话是 plan、acceptEdits 等模式时子 agent 会继承 | 和主 agent 共用同一个权限引擎 |
| 模型 | 可以给子 agent 指定不同的模型 | 和主 agent 用同一个 |
| 过程记录 | 子 agent 有自己的会话记录 | 不记录过程 |
| 定义字段 | 二十个左右的可选字段 | `name`、`description`、`tools`、`maxTurns` |

## 自检清单

- [ ] 能说出子 agent 的核心价值，并解释为什么不是“多一个脑子”。
- [ ] 能列出子 agent 和主 agent 共享和不共享的东西，并说出每一项的理由。
- [ ] 能写一个子 agent 定义，description 写清“什么时候派它”。
- [ ] 能用 Anthropic 给出的标准判断一个任务该不该拆成多 agent。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 子 agent 做的不是你想要的 | 主 agent 写的 prompt 缺背景，子 agent 看不到主对话 | Agent 工具的说明里已经要求“写清背景、目标和报告内容”；可以在 MINICODE.md 里再强调 |
| 子 agent 的报告太长，主上下文还是爆了 | 没有限制报告长度 | 在子 agent 的系统提示词里写明报告的长度上限 |
| 主 agent 从不派子 agent | description 没写清楚什么时候用 | 写成“什么情况下应该主动使用” |
| 子 agent 改了文件，主 agent 再改时报“文件被修改过” | 两边的“读过”状态不共享，修改时间对不上 | 这是正确的保护：让主 agent 重新 Read |
| 花费突然变高 | 一次派了很多子 agent | 用 `/cost` 观察；在子 agent 定义里限制 `maxTurns` |

## 进阶练习

1. 支持 `model` 字段：给 explore 子 agent 用一个更便宜的模型。
2. 把子 agent 的过程写进单独的会话文件（文件名里带上主会话 ID），方便事后调试。
3. 实现后台子 agent：Agent 工具加一个 `run_in_background` 参数，立刻返回一个 ID；再加一个工具按 ID 取结果。
4. 用第 11 章的评测框架做一个对比实验：同一组调研任务，单 agent 和“主 agent + explore 子 agent”各跑一遍，比较成功率和 token。

## 资料来源

- [Claude Code: Create custom subagents](https://code.claude.com/docs/en/sub-agents)
- [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：子 agent 架构
- [Agent SDK: Configure permissions](https://code.claude.com/docs/en/agent-sdk/permissions)：子 agent 的权限模式继承
