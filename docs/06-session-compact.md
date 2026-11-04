# 第 6 章：会话与压缩，中断了能接着做，上下文满了能继续

## 导读

到现在为止，minicode 的所有记忆都在内存里的 `messages` 列表中。这带来两个问题：

1. **进程一退出，什么都没了。** 一个做到一半的任务，关掉终端就得从头再来。
2. **列表只会越来越长。** 读几个大文件、跑几次测试，很快就会撞上模型的上下文上限；就算没撞上，越长越贵，模型的注意力也越分散。

Claude Code 用两个机制解决：**会话记录**（每条消息一产生就追加写进文件，随时可以恢复）和**上下文压缩**（快满时把旧的对话总结成一份交接摘要）。这一章把两者都实现出来，minicode 的“核心 harness”到这里就完整了。

学完你会有：

- `minicode/session.py`：追加写入的 JSONL 会话记录，`--continue` 和 `--resume`。
- `minicode/compact.py`：两级压缩：先免费清理旧的工具结果，不够再让模型写摘要。
- 能回答：进程在工具执行到一半时崩溃，恢复后会发生什么？压缩时应该留下什么、丢掉什么？

## Claude Code 是怎么做的

### 会话记录

[How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works) 写道：

> Each message, tool use, and result is written to a plaintext JSONL file under `~/.claude/projects/`.

会话跟目录绑定：`--continue` 接着当前目录最近的会话，`--resume` 按 ID 恢复，`--fork-session` 把历史复制到一个新会话里分叉出去。打开那些 JSONL 文件可以看到，每一行是一个条目，带有 `uuid` 和 `parentUuid`，条目之间串成一条链。

和会话记录相关的还有**检查点**：Claude Code 每次编辑文件前会给文件拍快照，出了问题可以按两次 Esc 回滚。文档特别提醒，检查点只覆盖本地文件的修改，对数据库、API、部署这类远程操作无能为力。

### 压缩

[context engineering 文章](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)描述了 Claude Code 的压缩方式：

> In Claude Code, for example, we implement this by passing the message history to the model to summarize and compress the most critical details. The model preserves architectural decisions, unresolved bugs, and implementation details while discarding redundant tool outputs or messages. The agent can then continue with this compressed context plus the five most recently accessed files.

文章还提到一种“最安全、最轻量”的压缩：**清理工具结果**。一个工具结果在历史里已经很靠前了，模型很少需要再看一遍原文。

官方文档补充了几点：

- 压缩是自动的，上下文快满时触发；也可以用 `/compact` 手动触发，并加上“重点保留什么”的说明。
- 摘要会保留用户的请求和关键代码片段，但**早期对话里的详细指令可能丢失**，所以长期有效的规则应该写进 CLAUDE.md，而不是只在对话里说一次。
- Agent SDK 文档写明 CLAUDE.md 的内容会重新注入，不会因为压缩而丢失。
- 如果某个输出大到压缩后马上又满，Claude Code 尝试几次之后就会停止自动压缩并报错，而不是无限循环。
- 有 `PreCompact` hook，可以在压缩前做点事，比如把完整记录存档。

## 第 1 步：会话记录

新建 `minicode/session.py`：

```python
"""会话记录：每条消息一产生就追加写进 JSONL，进程崩了也不丢。

对应 Claude Code 的 ~/.claude/projects/<项目>/<会话 id>.jsonl。每一行是一个条目，
带 uuid 和 parent_uuid，串成一条链。压缩不删除旧内容，只追加一个 compact 条目（新的消息快照），
恢复时从最后一个快照开始往后读。
"""
import datetime
import json
import uuid
from pathlib import Path

from .settings import project_dir


class Session:
    def __init__(self, cwd: Path, session_id: str | None = None, home: Path | None = None):
        self.id = session_id or str(uuid.uuid4())
        self.dir = project_dir(cwd, home)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.id}.jsonl"
        self.last_uuid: str | None = None

    def _write(self, kind: str, **data) -> None:
        entry = {"type": kind, "uuid": str(uuid.uuid4()), "parent_uuid": self.last_uuid,
                 "session_id": self.id, "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                 **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.last_uuid = entry["uuid"]

    def append(self, message: dict) -> None:
        self._write("message", message=message)

    def compact_boundary(self, messages: list[dict], trigger: str) -> None:
        self._write("compact", trigger=trigger, messages=messages)

    # ---------- 恢复 ----------
    @classmethod
    def resume(cls, cwd: Path, session_id: str | None = None, home: Path | None = None):
        """返回 (session, messages)。session_id 为空时取这个项目最近的一个会话（--continue）。"""
        directory = project_dir(cwd, home)
        if session_id is None:
            files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            if not files:
                raise FileNotFoundError("这个目录还没有任何会话")
            session_id = files[-1].stem
        session = cls(cwd, session_id, home)
        if not session.path.exists():
            raise FileNotFoundError(f"找不到会话 {session_id}")
        messages = []
        for line in session.path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue                                  # 最后一行可能在崩溃时只写了一半
            if entry["type"] == "compact":
                messages = list(entry["messages"])
            elif entry["type"] == "message":
                messages.append(entry["message"])
            session.last_uuid = entry["uuid"]
        return session, messages

    @staticmethod
    def list(cwd: Path, home: Path | None = None) -> list[tuple[str, str]]:
        """[(会话 id, 第一条用户消息的开头)]，新的在前。"""
        out = []
        files = sorted(project_dir(cwd, home).glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files:
            first = ""
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    msg = json.loads(line).get("message") or {}
                except json.JSONDecodeError:
                    continue
                if msg.get("role") == "user":
                    first = msg["content"].split("</system-reminder>")[-1].strip()[:40]
                    break
            out.append((f.stem, first))
        return out
```

设计要点：

- **只追加，不修改。** 每条消息一产生就写一行，进程随时崩溃，已经写下的都在。压缩也不删除旧内容，只是追加一个 `compact` 条目，里面是压缩后的完整消息列表。恢复时从最后一个快照开始往后读。
- **容忍写了一半的行。** 进程在写最后一行时被杀掉，那一行可能不完整，`resume` 跳过解析不了的行。
- **`parent_uuid` 串成链。** minicode 只是线性地读，但有了这条链，以后就能实现“从某一条消息分叉”（进阶练习 2）。

回顾第 2 章的 `agent.py`：`_append` 在追加消息的同时调用 `self.session.append`，这个接缝从现在开始生效。

### 恢复时最危险的情况

如果进程崩溃时，最后一条 assistant 消息里的工具调用还没有结果，恢复后直接请求模型会得到 400。`app.py` 恢复会话后调用 `agent.repair()`，给这些调用补上一条结果：“工具调用被中断，没有拿到结果，也不确定它是否已经产生影响”。

注意这句话的后半句。工具可能已经执行完了（比如文件已经删了），只是结果没来得及写下来。**不能**自动重新执行这个调用：删除、付款、发消息这类操作执行两次可能是灾难。正确的做法是告诉模型“不确定”，让它先检查现状再决定。

## 第 2 步：压缩

新建 `minicode/compact.py`：

```python
"""上下文压缩：上下文快满时，先做免费的清理，不够再让模型写摘要。

Claude Code 的做法（见 Anthropic 的 context engineering 文章）：把消息历史交给模型总结，
保留架构决策、没解决的 bug 和实现细节，丢掉冗余的工具输出，然后带着摘要和最近访问的五个文件继续。
"""
import json
from pathlib import Path

CLEARED = "[这条旧的工具结果已被清理以节省上下文。需要的话重新调用工具获取]"
MAX_FAILURES = 3

SUMMARY_PROMPT = """你要为一段 agent 工作会话写交接摘要。接手的人（还是你自己）只能看到这份摘要，看不到原始对话。
按下面的结构写，每一节都要有，没有内容就写“无”：

1. 用户的目标：用户要求做什么，逐条列出，尽量保留原话。
2. 重要决定和约定：选定的技术方案，用户明确提出的偏好和限制。
3. 文件：看过或改过哪些文件，每个文件改了什么、为什么。写完整路径。
4. 错误和修复：遇到过哪些错误、怎么解决的，用户纠正过你什么。
5. 用户说过的话：除工具结果以外的每一条用户消息，按顺序列出要点。
6. 当前进展：压缩前正在做什么，做到了哪一步。
7. 下一步：接下来要做的事。必须和用户最近的要求一致，不要自作主张开始新任务。

只写对话里真实出现过的内容，不要推测。"""


def rough_tokens(messages: list[dict]) -> int:
    """粗略估算：中英文混合时大约每 3 个字符一个 token。只用来判断“快满了没有”。"""
    return len(json.dumps(messages, ensure_ascii=False)) // 3


def clear_old_tool_results(messages: list[dict], keep: int) -> int:
    """把较早的长工具结果换成一句占位符。消息条数和 tool_call_id 都不变，所以不会破坏配对。"""
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    cleared = 0
    for m in tool_msgs[:-keep] if keep else tool_msgs:
        if len(m["content"]) > 200 and m["content"] != CLEARED:
            m["content"] = CLEARED
            cleared += 1
    return cleared


def render_transcript(messages: list[dict], max_tool_chars: int = 2000) -> str:
    lines = []
    for m in messages:
        if m["role"] == "user":
            lines.append(f"【用户】{m['content']}")
        elif m["role"] == "assistant":
            if m.get("content"):
                lines.append(f"【助手】{m['content']}")
            for c in m.get("tool_calls") or []:
                lines.append(f"【助手调用工具】{c['function']['name']}({c['function'].get('arguments', '')})")
        elif m["role"] == "tool":
            text = m["content"]
            if len(text) > max_tool_chars:
                text = text[:max_tool_chars] + f"...（截断，原长 {len(text)} 字符）"
            lines.append(f"【工具结果】{text}")
    return "\n\n".join(lines)


class Compactor:
    def __init__(self, llm, context_window: int = 128_000, threshold: float = 0.8,
                 keep_tool_results: int = 3, restore_files: int = 5, restore_chars: int = 20_000,
                 memory: str | None = None):
        self.llm = llm
        self.memory = memory          # 记忆文件的内容：它原本在第一条用户消息里，压缩后要重新附上
        self.window, self.threshold = context_window, threshold
        self.keep, self.restore_files, self.restore_chars = keep_tool_results, restore_files, restore_chars
        self.failures = 0

    def estimate(self, agent) -> int:
        """最准的是上一次请求返回的真实 usage；那之后新增的消息（主要是工具结果）按字符估算。"""
        if agent.last_usage is None or agent.mark > len(agent.messages):
            return rough_tokens(agent.messages)
        known = agent.last_usage.input_tokens + agent.last_usage.output_tokens
        return known + rough_tokens(agent.messages[agent.mark:])

    def maybe_compact(self, agent) -> dict | None:
        if self.failures >= MAX_FAILURES:          # 连续失败好几次，就别每轮都再浪费一次请求
            return None
        before = self.estimate(agent)
        limit = int(self.window * self.threshold)
        if before < limit:
            return None
        # 第一级：清理旧工具结果，不花钱
        if clear_old_tool_results(agent.messages, self.keep):
            agent.last_usage = None                # 旧的 usage 不再准确，改用字符估算
            after = self.estimate(agent)
            if after < limit:
                if agent.session:
                    agent.session.compact_boundary(agent.messages, "clear_tool_results")
                return {"type": "compact", "ok": True, "level": "clear_tool_results",
                        "before": before, "after": after}
        # 第二级：让模型写摘要
        return self.compact(agent, trigger="auto")

    def compact(self, agent, trigger: str = "manual", instructions: str = "") -> dict:
        if agent.hooks and (blocked := agent.hooks.pre_compact(trigger, instructions)):
            return {"type": "compact", "ok": False, "reason": blocked}
        before = self.estimate(agent)
        request = render_transcript(agent.messages[1:])
        if instructions:
            request += f"\n\n# 用户对这次摘要的额外要求\n{instructions}"
        try:
            reply = self.llm.complete([{"role": "system", "content": SUMMARY_PROMPT},
                                       {"role": "user", "content": request}])
            summary = (reply.message.get("content") or "").strip()
            agent.add_usage(reply.usage.input_tokens, reply.usage.output_tokens)
            if not summary:
                raise ValueError("模型返回了空摘要")
        except Exception as e:
            self.failures += 1
            return {"type": "compact", "ok": False, "reason": f"{type(e).__name__}: {e}"}
        self.failures = 0

        body = f"<system-reminder>\n{self.memory}\n</system-reminder>\n" if self.memory else ""
        body += f"此会话从一次上下文压缩后继续。下面是之前对话的摘要：\n\n{summary}"
        body += self._restored(agent)
        if trigger == "auto":     # 任务做到一半被压缩：让模型直接接着干
            agent.messages[:] = [agent.messages[0], {"role": "user", "content":
                                 body + "\n\n请直接从中断的地方继续，不要向用户重复确认。"}]
        else:                     # 两次提问之间手动压缩：补一条助手消息，避免连续两条 user 消息
            agent.messages[:] = [agent.messages[0], {"role": "user", "content": body},
                                 {"role": "assistant", "content": "我已读完摘要，可以继续。"}]
        agent.last_usage, agent.mark = None, len(agent.messages)
        if agent.session:
            agent.session.compact_boundary(agent.messages, trigger)
        return {"type": "compact", "ok": True, "level": "summary", "trigger": trigger,
                "before": before, "after": self.estimate(agent)}

    def _restored(self, agent) -> str:
        """把最近读过的几个文件的当前内容重新附上，再附上待办清单。"""
        out, budget = "", self.restore_chars
        for path in list(agent.ctx.read_files)[-self.restore_files:]:
            p = Path(path)
            if not p.is_file() or budget <= 0:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")[:budget // self.restore_files]
            agent.ctx.read_files[path] = p.stat().st_mtime
            out += f"\n\n# 最近读过的文件 {path}（当前内容，可能被截断）\n{text}"
            budget -= len(text)
        if agent.ctx.todos:
            items = "\n".join(f"- [{t['status']}] {t['content']}" for t in agent.ctx.todos)
            out += f"\n\n# 当前的待办清单\n{items}"
        return out
```

`maybe_compact` 每次请求模型之前都会被调用（回顾 `agent.py` 的 `_before_request`），它分三步：

1. **估算当前上下文有多大。** 最准的是上一次请求返回的真实 `usage`；那之后新增的消息（主要是工具结果）按每 3 个字符一个 token 粗略估算。没到阈值（默认是上下文窗口的 80%）就什么都不做。
2. **第一级：清理旧的工具结果。** 除了最近 3 条，较早的长工具结果换成一句占位符。消息条数和 `tool_call_id` 都不变，所以不会破坏“每个调用都有结果”的规则。这一步不花钱，往往就够了。
3. **第二级：让模型写摘要。** 把整个对话渲染成文本，交给模型按固定结构写交接摘要。然后把消息列表换成“系统提示词 + 一条包含摘要的用户消息”。

摘要提示词 `SUMMARY_PROMPT` 的结构是这一章最值得琢磨的地方。每一节都对应一种“压缩后最容易丢、丢了最麻烦”的信息：

| 摘要的一节 | 丢了会怎样 |
| --- | --- |
| 用户的目标（尽量保留原话） | 模型继续干活，但干的不是用户要的 |
| 重要决定和约定 | 重新讨论已经定下来的方案，或者违反用户明确提过的限制 |
| 文件和改动 | 不知道自己改过什么，重复修改或漏改 |
| 错误和修复 | 重新踩一遍同样的坑 |
| 用户说过的话 | 用户中途的纠正被遗忘 |
| 当前进展和下一步 | 不知道该从哪里接着做，或者自作主张开始新任务 |

摘要之外，`_restored` 还把三样东西重新附上：

- **记忆文件的内容。** 它原本在第一条用户消息里，压缩会把那条消息换掉。和 Claude Code 一样，压缩后重新注入。
- **最近读过的 5 个文件的当前内容。** 和 Claude Code 文章里说的“the five most recently accessed files”一致。模型压缩前正在看的代码，压缩后马上又能看到，不用重读。
- **待办清单**（第 7 章）。

还有两个保护：

- **连续失败 3 次就不再自动压缩。** 比如摘要请求本身就超长、或者服务一直报错时，不至于每一轮都浪费一次请求。
- **自动压缩和手动压缩的结尾不同。** 自动压缩发生在任务中途，最后一条是用户消息，模型直接接着干；手动压缩发生在两次提问之间，后面马上又会来一条用户消息，所以补一条简短的助手消息，避免连续两条用户消息（有的服务商不接受）。

## 第 3 步：接进 app.py

用下面的内容替换 `minicode/app.py`：

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
                  ask_user=ask_user)

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

变化：

- 根据 `resume` 和 `continue_session` 决定是新建会话还是恢复会话。恢复时用**当前**生成的系统提示词替换旧的（日期、git 状态可能已经变了），然后 `repair()`。
- hooks 拿到了真实的会话 ID 和会话记录路径，第 4 章的 `require_tests.py` 从现在起能正常工作了。
- 创建 `Compactor`，上下文窗口大小从环境变量 `MINICODE_CONTEXT_WINDOW` 读，默认 128000。请按你用的模型的实际窗口设置。记忆内容同时交给压缩器，压缩后重新附上。

## 跑起来

**会话恢复。** 让 minicode 做一个多步任务，中途按 Ctrl+C 打断，然后退出：

```bash
uv run python -m minicode --cwd playground
```

接着恢复刚才的会话，问它刚才做到哪了：

```bash
uv run python -m minicode --cwd playground --continue
```

也可以在交互模式里输入 `/resume` 列出最近的会话，再用 `--resume <id>` 恢复指定的那个。看看会话记录长什么样：

```bash
ls ~/.minicode/projects/
```

```bash
tail -n 3 ~/.minicode/projects/*playground/*.jsonl | head -c 1500
```

**观察压缩。** 把上下文窗口故意设得很小，很快就能看到压缩：

```bash
MINICODE_CONTEXT_WINDOW=6000 uv run python -m minicode --cwd playground
```

让它连续读几个文件、跑几次命令，你会看到 `[上下文压缩 clear_tool_results：…]` 或 `[上下文压缩 summary：…]`。随时输入 `/context` 查看各部分占了多少，输入 `/compact 重点保留改过的文件` 手动压缩。压缩之后问它“我们刚才在做什么”，检查摘要有没有丢掉重要信息。

## 离线测试

新建 `tests/test_ch06.py`：

```python
from minicode.agent import Agent
from minicode.compact import CLEARED, Compactor, clear_old_tool_results
from minicode.permissions import PermissionEngine
from minicode.session import Session
from minicode.testing import FakeLLM, call
from minicode.tools import core_tools


def test_resume_after_crash(tmp_path):
    session = Session(tmp_path)
    agent = Agent(FakeLLM([call("Glob", pattern="*")]), core_tools(), "s", cwd=tmp_path, session=session)
    session.append(agent.messages[0])
    events = agent.query("列一下文件")
    next(events), next(events)                # 模拟：执行完一个工具后进程就崩了
    resumed, messages = Session.resume(tmp_path)              # --continue：取最近的会话
    assert resumed.id == session.id and messages == agent.messages
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool"]


def test_clear_old_tool_results_keeps_pairs():
    msgs = [{"role": "tool", "tool_call_id": str(i), "content": "x" * 500} for i in range(4)]
    assert clear_old_tool_results(msgs, keep=1) == 3
    assert [m["content"] == CLEARED for m in msgs] == [True, True, True, False]
    assert [m["tool_call_id"] for m in msgs] == ["0", "1", "2", "3"]


def test_auto_compact_keeps_working(tmp_path):
    (tmp_path / "a.txt").write_text("文件 A 的内容")
    llm = FakeLLM([call("Read", file_path="a.txt"),
                   call("Bash", command="python -c \"print('y' * 3000)\""),
                   "## 摘要\n用户要读 a.txt", "继续完成了"])
    agent = Agent(llm, core_tools(), "s", cwd=tmp_path, session=Session(tmp_path),
                  permissions=PermissionEngine(mode="bypassPermissions", workdirs=[tmp_path]),
                  compactor=Compactor(llm, context_window=1500, threshold=0.5, keep_tool_results=1,
                                      memory="项目约定：用中文回答"))
    events = list(agent.query("读 a.txt"))
    assert any(e["type"] == "compact" and e.get("level") == "summary" for e in events)
    assert events[-1]["text"] == "继续完成了"
    assert "用户要读 a.txt" in agent.messages[1]["content"]
    assert "文件 A 的内容" in agent.messages[1]["content"]        # 最近读过的文件被重新附上
    assert "项目约定：用中文回答" in agent.messages[1]["content"]  # 记忆文件也重新附上
```

```bash
uv run python -m pytest tests -q
```

`test_resume_after_crash` 用生成器的特性模拟“执行到一半就崩了”：只取了两个事件就不再继续，此时会话文件里已经有四条消息，恢复出来的内容完全一样。

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| 会话目录 | `~/.claude/projects/<路径>/` | `~/.minicode/projects/<路径>/` |
| 分叉、回滚 | `--fork-session`、`/branch`，编辑前的文件检查点可以回滚 | 都没有 |
| token 计数 | 用 API 返回的真实用量 | 真实用量 + 粗略估算 |
| 压缩后恢复的内容 | 摘要、最近访问的文件、CLAUDE.md、用过的 skill 等 | 摘要、最近读过的文件、记忆文件、待办清单 |
| 压缩的触发点 | 接近上限时，具体阈值由 Claude Code 决定 | 窗口的 80%，可配置 |
| 自定义压缩重点 | `/compact` 加说明，或在 CLAUDE.md 里写 “Compact Instructions” 一节 | 只支持 `/compact` 加说明 |

## 自检清单

- [ ] 能说出会话记录为什么要“只追加、不修改”。
- [ ] 能解释为什么恢复时不能自动重新执行被中断的工具调用。
- [ ] 能说出两级压缩各自的代价和效果。
- [ ] 能逐条解释摘要提示词的七节分别防的是什么。
- [ ] 知道压缩后有哪些东西被重新附上，为什么是这些。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `--continue` 找不到会话 | 会话按目录区分，`--cwd` 和上次不一样 | 用同一个 `--cwd`；或者用 `/resume` 查 ID |
| 恢复后第一次请求就报 400 | 最后一条 assistant 消息的工具调用没有结果 | 确认 `app.py` 里调用了 `agent.repair()` |
| 每一轮都在压缩 | 窗口设得太小，或者某个工具结果本身就超过窗口 | 调大 `MINICODE_CONTEXT_WINDOW`；减小工具输出上限 |
| 压缩后模型忘了用户的某条要求 | 摘要漏掉了 | 长期有效的要求写进 MINICODE.md；或者 `/compact` 时说明重点 |
| 会话目录越来越大 | 每个会话一个文件，从不删除 | 定期清理很旧的会话（进阶练习 4） |

## 进阶练习

1. 写一个 `/rewind` 命令：在会话记录里追加一个快照，回到倒数第 N 条用户消息之前的状态。思考：文件的修改怎么办？
2. 实现 `--fork-session`：复制历史到一个新的会话 ID，从那里分叉继续。
3. 把压缩做成可选的“只清理工具结果，不写摘要”，比较两种方式下模型完成长任务的成功率。
4. 加一个 `minicode sessions --prune 30` 子命令，删除 30 天以前的会话记录。

## 资料来源

- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)：会话、检查点、上下文管理
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)：压缩、清理工具结果
- [How the agent loop works（Agent SDK）](https://code.claude.com/docs/en/agent-sdk/agent-loop)：自动压缩、PreCompact
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)：`--continue`、`--resume`
