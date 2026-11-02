## 导读：学完你会得到什么

这一关的目标是看懂一个成熟 agent 系统的骨架，并自己做一个小的。学完你会有：

- 一份“五大组件定位图”，拿到任何 agent 项目都知道先看哪里。
- 一个约 300 行的 `harness.py`，带权限闸门、hooks、会话恢复、上下文压缩和完整 trace。
- 一组对比数据：同一个任务、同一个模型，裸循环和 harness 的结果差在哪。
- 一份能交付的 demo：README、运行步骤、示例输入输出和失败记录。

核心只有一句话：**模型决定想做什么，harness 决定能不能做、怎么记录、出错了怎么办。** 上一关你写的 `CallGuard`、`trim_context`、`SessionStore`，其实已经是 harness 的零件了，这一关把它们组织起来。

学习路线和 todolist 一一对应：

1. 选一个系统，读懂目录结构。
2. 找出 agent loop、tool registry、permission gate、session store、context compaction。
3. 跑通最小示例，加一个自己的工具。
4. 观察一次完整 trace，解释每一步为什么发生。
5. 同一任务分别用裸循环和 harness 实现，对比差异。

前置知识：做完 Stage 1，最好也做完 Stage 2。

## 第 0 步：先选一个系统，别贪多

推荐路线：**`learn-claude-code` 为主，Claude Code 官方文档为对照。**

`learn-claude-code` 是 Python 写的，每章一个 `code.py`，从 153 行的 s01 逐章加机制，一路到 3330 行的集成版 s15。你每天用的 Claude Code 又正好是它拆解的对象，读完可以马上对照实际体验。

| 系统 | 选它，如果你想…… | 上手难度 |
| --- | --- | --- |
| [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) | 从零理解 coding agent 的每个机制（**本教程采用**） | 低 |
| [Claude Code 文档](https://code.claude.com/docs/en/overview) | 看产品级设计：权限、hooks、subagents、MCP | 低 |
| [hello-agents](https://github.com/datawhalechina/hello-agents) | 系统补原理，中文教程（`docs/` 里那份 PDF 就是它） | 低 |
| [claw0](https://github.com/shareAI-lab/claw0) | 学长期运行的网关：session、channel、heartbeat、delivery | 中 |
| [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) | 学状态图和可恢复执行 | 中 |
| [OpenClaw](https://github.com/openclaw/openclaw)、[Hermes Agent](https://github.com/NousResearch/hermes-agent) | 看成熟的本地个人 agent（代码量大，建议放到后面） | 高 |
| [CyberClaw](https://github.com/ttguy0707/CyberClaw) | 学全行为审计和两段式安全调用 | 中 |

## 第 1 步：读懂目录结构

把项目放到你的代码目录之外，它不属于这个仓库：

```bash
git clone --depth 1 https://github.com/shareAI-lab/learn-claude-code ~/Desktop/learn-claude-code
```

读陌生项目时，不要从第一个文件读到最后一个，按这个顺序更省力：

1. **README**：作者认为这个项目是什么。
2. **依赖文件**：它只依赖 `anthropic`、`python-dotenv`、`pyyaml`，说明核心逻辑都是手写的，很适合学习。
3. **入口文件**：`if __name__ == "__main__"` 在哪里。
4. **文件大小**：`wc -l s*/code.py`。代码量跳涨的地方，通常就是复杂度真正所在。

这个仓库的结构一目了然，每个 `sNN_主题/` 目录里都有 `code.py` 和中文讲解 `README.zh.md`：

```text
s01_agent_loop/          153 行   循环
s02_tool_use/            208 行   工具表
s03_permission/          271 行   权限
s04_hooks/               282 行   扩展点
s05_todo_write/          370 行   先计划后执行
s06_subagent/            385 行   上下文隔离
s07_skill_loading/       401 行   按需加载知识
s08_context_compact/     619 行   上下文压缩
s09_memory/              786 行   记忆
s10-s14                          任务、后台、定时、团队、MCP
s15_integrated_harness/ 3330 行   全部集成
s16、s17                         工作流运行时、目标循环
agents/                          旧版 12 章，可以先忽略
```

建议顺序：s01 → s02 → s03 → s04 → s08，然后跳到 s15 看它们怎么合在一起。其他章节等用到再读。

## 第 2 步：找到五大组件

几乎所有 agent harness 都有这五样东西，只是名字不同。这张表是这一关最值得记住的内容：

| 组件 | 它回答的问题 | learn-claude-code | Claude Code | 本教程 `harness.py` |
| --- | --- | --- | --- | --- |
| Agent loop | 什么时候继续，什么时候停？ | `s01` 的 `agent_loop` | [agentic loop](https://code.claude.com/docs/en/how-claude-code-works) | `Harness.run` |
| Tool registry | 模型能用什么？名字怎么对应到函数？ | `s02` 的 `TOOLS`、`TOOL_HANDLERS` | [工具集](https://code.claude.com/docs/en/how-claude-code-works) | `ToolRegistry` |
| Permission gate | 这个调用允许吗？ | `s03` 的 `check_permission` | [权限规则和模式](https://code.claude.com/docs/en/permissions) | `PermissionGate` |
| Session store | 中断后怎么接着做？ | `s08` 的 `write_transcript`、`s16` 的 journal | `~/.claude/projects/` 下的 JSONL | `SessionStore` |
| Context compaction | 上下文满了怎么办？ | `s08` 的 `ContextCompactor` | 自动压缩和 `/compact` | `micro_compact`、`_compact` |

换一个项目时，可以用搜索快速定位。这些关键词在大多数项目里都管用：

```bash
rg -n "while True|stop_reason|finish_reason|tool_calls" --type py    # 循环
```

```bash
rg -n "TOOL_HANDLERS|register|def tool|tools\s*=" --type py          # 工具表
```

```bash
rg -n "permission|approve|allow|deny|sandbox" --type py              # 权限
```

```bash
rg -n "session|transcript|resume|checkpoint|\.jsonl" --type py       # 会话
```

```bash
rg -n "compact|summar|truncate|token.*(limit|budget)" --type py      # 压缩
```

读每个组件时，带着这几个问题，比逐行读更有收获：

- **循环**：除了“模型不再调工具”，还有哪些退出条件？
- **工具表**：工具出错后，错误怎么回到模型那里？
- **权限**：判断发生在执行之前吗？能绕过去吗？
- **会话**：进程崩溃时，已经发生的事情丢不丢？
- **压缩**：压缩后还有 `tool` 消息落单吗？原始内容去哪了？

顺带看一个值得思考的细节。s01 的 `run_bash` 用的是黑名单：

```python
dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
```

`rm -rf ~` 不在名单里，`rm -r /` 也不在。这对教学代码没问题，但你要明白：**黑名单挡得住手滑，挡不住漏掉的写法。** 所以 s03 才要加权限管线，真实产品还要加沙箱。

## 第 3 步：跑通最小示例，加一个自己的工具

> **先看这条安全提醒：** 这个项目的 `bash` 工具会在当前目录真实执行命令，而且前面看到了，它的黑名单并不完整。请在一个没有重要文件的目录里运行，最好用容器。本教程的作者只阅读了这部分源码，没有实际运行过它，所以下面的步骤如果和项目最新版有出入，请以项目 README 为准。

项目用的是 Anthropic SDK。DeepSeek 提供 Anthropic 兼容接口，所以不一定需要 Claude 的 key。在项目目录里准备环境：

```bash
cd ~/Desktop/learn-claude-code
```

```bash
uv venv --python 3.12
```

```bash
uv pip install -r requirements.txt
```

新建 `.env`，内容如下（用 Claude 的话，只留前两行，换成你的 Claude key 和模型名）：

```bash
ANTHROPIC_API_KEY=你的DeepSeek-key
MODEL_ID=deepseek-flash
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
```

运行第一章，问它“当前目录有几个文件？”：

```bash
uv run python s01_agent_loop/code.py
```

项目自己也提醒过，“兼容 Anthropic”不等于行为完全相同。如果换成 DeepSeek 后报错，先查参数和思考模式相关的差异。

### 加一个自己的工具

打开 `s02_tool_use/code.py`，改三处。这三处正好对应 Stage 1 讲过的“函数、说明书、注册表”：

```python
# ① 在 TOOLS 定义之前加函数。safe_path 是文件里已有的路径检查
def run_count_lines(path: str) -> str:
    """统计文件行数。"""
    lines = safe_path(path).read_text(encoding="utf-8").splitlines()
    return f"{path}: {len(lines)} 行"

# ② 在 TOOLS 列表里加说明书
    {"name": "count_lines", "description": "统计一个文本文件有多少行。",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},

# ③ 在 TOOL_HANDLERS 字典里加映射
    "count_lines": run_count_lines,
```

问它 `README.md 有多少行？`，看到模型选了 `count_lines` 就成功了。再试试把 description 改得很含糊，看模型还会不会选它。

## 第 4 步：自己写一个 harness，看懂一次完整 trace

读别人的代码容易“看懂了但写不出”。所以我们把五大组件写成一个最小版本，顺便加上 learn-claude-code 前几章没有的 trace，方便调试。

先安装依赖，建好目录：

```bash
uv add openai
```

```bash
mkdir -p agent-life-from-zero/stage3
```

### 4.1 核心代码

新建 `harness.py`：

```python
"""一个可调试的迷你 harness：循环、工具注册表、权限闸门、hooks、会话存储、上下文压缩、trace。

模型负责决定做什么，这个文件负责决定“能不能做、怎么记录、出了问题怎么办”。
"""
import fnmatch
import inspect
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from openai import OpenAI

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


# ---------- 工具注册表：加一个工具 = 写一个函数 + 加一行装饰器 ----------
@dataclass
class Tool:
    name: str
    description: str
    fn: Callable
    risk: str                # read / write / dangerous，权限闸门按它决定默认行为
    parameters: dict


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def tool(self, risk: str = "read"):
        """从函数签名和 docstring 自动生成说明书，名字和 schema 不会对不上。"""
        def decorator(fn):
            sig = inspect.signature(fn)
            props = {n: {"type": _JSON_TYPES.get(p.annotation, "string")} for n, p in sig.parameters.items()}
            required = [n for n, p in sig.parameters.items() if p.default is inspect.Parameter.empty]
            self.tools[fn.__name__] = Tool(fn.__name__, inspect.getdoc(fn) or "", fn, risk,
                                           {"type": "object", "properties": props, "required": required})
            return fn
        return decorator

    def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.parameters}}
                for t in self.tools.values()]


# ---------- 权限闸门：和 Claude Code 一样，按 deny → ask → allow 的顺序匹配 ----------
@dataclass
class Decision:
    allowed: bool
    reason: str


class PermissionGate:
    """规则写成 工具名(参数通配符)，例如 delete_file(*.tmp)。默认行为看工具的风险等级。"""

    def __init__(self, deny=(), ask=(), allow=(), mode: str = "default", ask_user=None):
        self.deny, self.ask, self.allow = list(deny), list(ask), list(allow)
        self.mode = mode                         # default / plan（只读）/ dontask（要问的一律拒绝）
        self.ask_user = ask_user or self._prompt

    @staticmethod
    def _prompt(tool: str, args: dict) -> bool:
        return input(f"\n允许 {tool}({json.dumps(args, ensure_ascii=False)}) 吗？[y/N] ").strip().lower() == "y"

    @staticmethod
    def _match(rules: list[str], tool: str, args: dict) -> str | None:
        first = str(next(iter(args.values()), ""))
        for rule in rules:
            name, _, pattern = rule.partition("(")
            if name == tool and fnmatch.fnmatch(first, pattern.rstrip(")") or "*"):
                return rule
        return None

    def check(self, tool: Tool, args: dict) -> Decision:
        if rule := self._match(self.deny, tool.name, args):
            return Decision(False, f"被规则 {rule} 禁止")
        if self.mode == "plan" and tool.risk != "read":
            return Decision(False, "plan 模式只能使用只读工具")
        need_ask = self._match(self.ask, tool.name, args)
        if not need_ask and self._match(self.allow, tool.name, args):
            return Decision(True, "命中 allow 规则")
        if not need_ask and tool.risk == "read":
            return Decision(True, "只读工具默认放行")
        if self.mode == "dontask":
            return Decision(False, "非交互模式，需要确认的操作一律拒绝")
        return Decision(True, "用户批准") if self.ask_user(tool.name, args) else Decision(False, "用户拒绝")


# ---------- Trace：每一步发生了什么，都写进一个 JSONL ----------
class Tracer:
    def __init__(self, run_id: str, directory: Path = Path(".traces")):
        directory.mkdir(exist_ok=True)
        self.path = directory / f"{run_id}.jsonl"
        self.run_id = run_id

    def event(self, step: int, kind: str, **data) -> None:
        record = {"ts": round(time.time(), 3), "run_id": self.run_id, "step": step, "kind": kind, **data}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


# ---------- 会话存储：只追加。压缩也只是追加一条记录，原始内容永远留着 ----------
class SessionStore:
    def __init__(self, session_id: str, directory: Path = Path(".sessions")):
        directory.mkdir(exist_ok=True)
        self.path = directory / f"{session_id}.jsonl"

    def _write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def add_message(self, message: dict) -> None:
        self._write({"type": "message", "message": message})

    def add_compaction(self, messages: list[dict]) -> None:
        self._write({"type": "compact", "messages": messages})

    def load(self) -> list[dict]:
        """回放文件：遇到 compact 就从那个快照重新开始。"""
        if not self.path.exists():
            return []
        messages = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if record["type"] == "compact":
                messages = list(record["messages"])
            else:
                messages.append(record["message"])
        return messages


# ---------- 上下文压缩：先做免费的（省略旧工具结果），不够再花钱让模型写摘要 ----------
def context_size(messages: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


def micro_compact(messages: list[dict], keep_recent: int = 3, min_chars: int = 400) -> int:
    """把较早的长工具结果换成一句话，消息和 tool_call_id 不动。返回省略了几条。"""
    tool_msgs = [m for m in messages if m["role"] == "tool"]
    count = 0
    for m in tool_msgs[:-keep_recent] if keep_recent else tool_msgs:
        if len(m["content"]) > min_chars:
            m["content"] = f"[已省略 {len(m['content'])} 字符的旧工具结果，需要时请重新调用工具]"
            count += 1
    return count


def safe_cut(messages: list[dict], keep_last: int) -> int:
    """找一个切分点，保证保留段不以落单的 tool 消息开头。"""
    cut = max(1, len(messages) - keep_last)
    while cut < len(messages) and messages[cut]["role"] == "tool":
        cut += 1
    return cut


SUMMARY_PROMPT = """把下面的对话压缩成一份交接摘要，接手的人看不到原始对话。必须包含：
1. 用户的原始目标（尽量保留原话）；2. 已经做了什么、结果如何；3. 改动过哪些文件；
4. 遇到的错误和被拒绝的操作；5. 还没完成的事。不要新增对话里没有的内容。"""


# ---------- 主循环 ----------
@dataclass
class RunResult:
    status: str               # done / max_steps / timeout / model_error
    text: str
    steps: int = 0
    tokens: int = 0
    session_id: str = ""
    trace_path: str = ""


@dataclass
class Harness:
    registry: ToolRegistry
    gate: PermissionGate
    system: str = "你是一个谨慎的助手。操作被拒绝时不要换个方式重试，向用户说明即可。"
    client: object = None
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-flash"))
    max_steps: int = 10
    total_timeout: float = 120
    tool_output_limit: int = 4000
    compact_at: int = 24000           # 超过就压缩。要明显大于 keep_last 段的最大体积，否则每轮都会压缩
    keep_last: int = 6                # 压缩时原样保留最后几条消息
    pre_tool_hooks: list = field(default_factory=list)   # (工具名, 参数) -> None 或拒绝理由
    post_tool_hooks: list = field(default_factory=list)  # (工具名, 参数, 结果) -> 新结果

    def __post_init__(self):
        self.client = self.client or OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"),
                                            timeout=60, max_retries=2)

    def _execute(self, call, step: int, tracer: Tracer) -> str:
        name = call.function.name
        started = time.monotonic()
        tool = self.registry.tools.get(name)
        if tool is None:
            result = f"错误：没有工具 {name}，可用工具：{list(self.registry.tools)}"
            tracer.event(step, "tool_error", tool=name, error="unknown_tool")
            return result
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError as e:
            tracer.event(step, "tool_error", tool=name, error="bad_json")
            return f"错误：参数不是合法 JSON（{e}）"

        for hook in self.pre_tool_hooks:
            if reason := hook(name, args):
                tracer.event(step, "permission", tool=name, args=args, allowed=False, reason=f"hook: {reason}")
                return f"已拒绝：{reason}"
        decision = self.gate.check(tool, args)
        tracer.event(step, "permission", tool=name, args=args, allowed=decision.allowed, reason=decision.reason)
        if not decision.allowed:
            return f"已拒绝：{decision.reason}。不要换一种方式重试，向用户说明。"

        try:
            result = str(tool.fn(**args))
            ok = True
        except Exception as e:
            result, ok = f"错误：{type(e).__name__}: {e}", False
        for hook in self.post_tool_hooks:
            result = hook(name, args, result)
        if len(result) > self.tool_output_limit:
            result = result[:self.tool_output_limit] + f"\n...[已截断，共 {len(result)} 字符]"
        tracer.event(step, "tool_result", tool=name, ok=ok, ms=round((time.monotonic() - started) * 1000),
                     preview=result[:200])
        return result

    def _compact(self, messages: list[dict], step: int, tracer: Tracer, session: SessionStore) -> list[dict]:
        before = context_size(messages)
        if before <= self.compact_at:
            return messages
        elided = micro_compact(messages)
        if context_size(messages) <= self.compact_at:
            tracer.event(step, "compact", level="micro", elided=elided, before=before, after=context_size(messages))
            session.add_compaction(messages)
            return messages
        cut = safe_cut(messages, self.keep_last)
        old = json.dumps(messages[1:cut], ensure_ascii=False)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": old}])
            summary = resp.choices[0].message.content or ""
        except Exception as e:                   # 压缩失败不能让任务崩溃，下一轮还有机会
            tracer.event(step, "compact_failed", error=f"{type(e).__name__}: {e}")
            return messages
        compacted = [messages[0], {"role": "user", "content": f"[之前对话的摘要]\n{summary}"}] + messages[cut:]
        tracer.event(step, "compact", level="summary", before=before, after=context_size(compacted), dropped=cut - 1)
        session.add_compaction(compacted)
        return compacted

    def run(self, task: str, session_id: str | None = None) -> RunResult:
        session_id = session_id or uuid.uuid4().hex[:8]
        run_id = f"{session_id}-{uuid.uuid4().hex[:4]}"
        tracer, session = Tracer(run_id), SessionStore(session_id)
        messages = session.load()
        if not messages:
            messages = [{"role": "system", "content": self.system}]
            session.add_message(messages[0])
        user = {"role": "user", "content": task}
        messages.append(user)
        session.add_message(user)
        tracer.event(0, "run_start", task=task, session_id=session_id, resumed=len(messages) > 2,
                     mode=self.gate.mode, tools=list(self.registry.tools))

        deadline, tokens = time.monotonic() + self.total_timeout, 0

        def finish(status: str, text: str, step: int) -> RunResult:
            tracer.event(step, "run_end", status=status, steps=step, tokens=tokens)
            return RunResult(status, text, step, tokens, session_id, str(tracer.path))

        for step in range(1, self.max_steps + 1):
            if time.monotonic() > deadline:
                return finish("timeout", f"[超时] 超过 {self.total_timeout} 秒", step - 1)
            messages = self._compact(messages, step, tracer, session)
            tracer.event(step, "model_request", messages=len(messages), chars=context_size(messages))
            started = time.monotonic()
            try:
                resp = self.client.chat.completions.create(model=self.model, messages=messages,
                                                           tools=self.registry.schemas())
            except Exception as e:
                tracer.event(step, "model_error", error=f"{type(e).__name__}: {e}")
                return finish("model_error", f"[调用模型失败] {type(e).__name__}: {e}", step)
            msg = resp.choices[0].message
            tokens += resp.usage.total_tokens if resp.usage else 0
            tracer.event(step, "model_response", ms=round((time.monotonic() - started) * 1000),
                         tokens=resp.usage.total_tokens if resp.usage else None,
                         text=(msg.content or "")[:200],
                         tool_calls=[{"name": c.function.name, "args": c.function.arguments} for c in msg.tool_calls or []])
            assistant = msg.model_dump(exclude_none=True)
            messages.append(assistant)
            session.add_message(assistant)
            if not msg.tool_calls:
                return finish("done", msg.content or "", step)
            for call in msg.tool_calls:
                result = self._execute(call, step, tracer)
                tool_msg = {"role": "tool", "tool_call_id": call.id, "content": result}
                messages.append(tool_msg)
                session.add_message(tool_msg)
        return finish("max_steps", f"[停止] 已达到最大步数 {self.max_steps}", self.max_steps)
```

几个设计值得对照着看：

**权限顺序和 Claude Code 一样。** [官方文档](https://code.claude.com/docs/en/permissions)写明按 deny → ask → allow 的顺序匹配，第一个匹配的规则决定结果，规则再具体也改变不了这个顺序。这样一来，宽泛的 allow 永远打不穿 deny。

**拒绝也是一种结果。** 被拒绝的调用不会让程序崩溃，而是告诉模型“已拒绝”和原因。模型可能会想绕路，比如 `delete_file` 被拒后尝试别的工具，所以返回文字里写明了“不要换一种方式重试”。

**会话只追加，不修改。** 压缩时只是写入一条新的快照记录，原始消息还留在文件前面。Claude Code 同样把每条消息写进 JSONL 文件。

**压缩分两级。** 先做免费的：把旧工具结果换成一句话。不够再花钱让模型写摘要。摘要提示词明确要求保留用户目标、已做的事、改过的文件和错误，这也是压缩后最容易丢的内容。

**`compact_at` 要留足余量。** 如果保留段本身就超过阈值，压缩后马上又超，每一轮都会触发一次摘要。Claude Code 也遇到同样的问题，它的做法是连续几次压不下来就停止，并报错。

### 4.2 工具和入口

新建 `demo_tools.py`：

```python
"""演示用的工具，读、写、删三种风险各有一个。所有路径都限制在 workspace 内。"""
from pathlib import Path

from harness import ToolRegistry

WORKDIR = Path("workspace").resolve()
registry = ToolRegistry()


def _safe(path: str) -> Path:
    p = (WORKDIR / path).resolve()
    if not p.is_relative_to(WORKDIR):
        raise PermissionError("路径必须在 workspace 内")
    return p


@registry.tool(risk="read")
def list_files(pattern: str = "*") -> str:
    """列出 workspace 里匹配通配符的文件，例如 *.tmp"""
    return "\n".join(sorted(str(f.relative_to(WORKDIR)) for f in WORKDIR.rglob(pattern) if f.is_file())) or "（没有文件）"


@registry.tool(risk="read")
def read_file(path: str) -> str:
    """读取 workspace 里的文本文件"""
    return _safe(path).read_text(encoding="utf-8")


@registry.tool(risk="write")
def write_file(path: str, content: str) -> str:
    """写入文本文件，已存在就覆盖"""
    target = _safe(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"已写入 {path}（{len(content)} 字符）"


@registry.tool(risk="dangerous")
def delete_file(path: str) -> str:
    """删除一个文件，无法恢复"""
    _safe(path).unlink()
    return f"已删除 {path}"


def reset_workspace() -> None:
    """把演示目录恢复到初始状态，方便反复实验。"""
    WORKDIR.mkdir(exist_ok=True)
    for f in WORKDIR.rglob("*"):
        if f.is_file():
            f.unlink()
    (WORKDIR / "cache_01.tmp").write_text("临时文件", encoding="utf-8")
    (WORKDIR / "cache_02.tmp").write_text("临时文件", encoding="utf-8")
    (WORKDIR / "lab_notes.md").write_text("2026-10-30 样本B=1.37，这是唯一的原始记录。\n", encoding="utf-8")
```

新建 `demo.py`：

```python
"""运行 harness。

uv run --env-file ../../.env demo.py "清理 workspace 里的临时文件，然后告诉我还剩什么"
继续上一次的会话：加 --resume 会话ID。只读试跑：加 --mode plan。
"""
import argparse

from demo_tools import WORKDIR, registry, reset_workspace
from harness import Harness, PermissionGate


def block_secrets(tool: str, args: dict) -> str | None:
    """pre_tool hook 示例：不管权限规则怎么写，都不碰名字里带 secret 的文件。"""
    return "禁止访问 secret 文件" if "secret" in str(args).lower() else None


def build_gate(mode: str = "default", ask_user=None) -> PermissionGate:
    return PermissionGate(deny=["delete_file(*.md)"],           # 实验记录永远不能删
                          allow=["delete_file(*.tmp)"],         # 临时文件可以直接删
                          mode=mode, ask_user=ask_user)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("task")
    parser.add_argument("--resume", help="要继续的会话 ID")
    parser.add_argument("--mode", default="default", choices=["default", "plan", "dontask"])
    args = parser.parse_args()
    if not WORKDIR.exists():
        reset_workspace()
    harness = Harness(registry, build_gate(args.mode), pre_tool_hooks=[block_secrets])
    result = harness.run(args.task, session_id=args.resume)
    print(f"\n{result.text}\n\n状态：{result.status}  步数：{result.steps}  tokens：{result.tokens}")
    print(f"继续这个会话：--resume {result.session_id}")
    print(f"查看 trace：uv run trace_view.py {result.trace_path}")
```

新建 `trace_view.py`：

```python
"""把 trace JSONL 打印成时间线。用法：uv run trace_view.py .traces/xxx.jsonl"""
import json
import sys
from pathlib import Path


def describe(e: dict) -> str:
    kind = e["kind"]
    if kind == "run_start":
        return f"开始  任务={e['task']!r} 模式={e['mode']} 恢复会话={e['resumed']}"
    if kind == "model_request":
        return f"请求模型  {e['messages']} 条消息，{e['chars']} 字符"
    if kind == "model_response":
        calls = ", ".join(f"{c['name']}({c['args']})" for c in e["tool_calls"])
        return f"模型回复  {e['ms']}ms {e['tokens']} tokens -> " + (f"调用 {calls}" if calls else f"回答 {e['text']!r}")
    if kind == "permission":
        return f"权限 {'放行' if e['allowed'] else '拒绝'}  {e['tool']}：{e['reason']}"
    if kind == "tool_result":
        return f"工具{'完成' if e['ok'] else '失败'}  {e['tool']} {e['ms']}ms -> {e['preview']!r}"
    if kind == "compact":
        return f"压缩上下文  方式={e['level']} {e['before']} -> {e['after']} 字符"
    if kind == "run_end":
        return f"结束  状态={e['status']} 步数={e['steps']} tokens={e['tokens']}"
    return f"{kind}  {e.get('error', '')} {e.get('tool', '')}"


def main(path: str) -> None:
    events = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    start = events[0]["ts"]
    for e in events:
        print(f"{e['ts'] - start:6.2f}s  第{e['step']}轮  {describe(e)}")
    denied = [e for e in events if e["kind"] == "permission" and not e["allowed"]]
    errors = [e for e in events if e["kind"] in {"tool_error", "model_error", "compact_failed"}
              or (e["kind"] == "tool_result" and not e["ok"])]
    print(f"\n小结：工具调用 {sum(e['kind'] == 'permission' for e in events)} 次，被拒绝 {len(denied)} 次，出错 {len(errors)} 次")


if __name__ == "__main__":
    main(sys.argv[1])
```

### 4.3 解读一次 trace

下面这段由第 5 步的 `compare.py` 生成。用的是剧本里的假模型，所以耗时都接近 0，但事件顺序和真实运行完全相同：

```text
  0.00s  第0轮  开始  任务='清理 workspace 里的临时文件，然后告诉我还剩什么' 模式=default 恢复会话=False
  0.00s  第1轮  请求模型  2 条消息，126 字符
  0.00s  第1轮  模型回复  0ms 120 tokens -> 调用 list_files({"pattern": "*"})
  0.00s  第1轮  权限 放行  list_files：只读工具默认放行
  0.00s  第1轮  工具完成  list_files 0ms -> 'cache_01.tmp\ncache_02.tmp\nlab_notes.md'
  0.00s  第2轮  请求模型  4 条消息，390 字符
  0.00s  第2轮  模型回复  0ms 120 tokens -> 调用 delete_file({"path": "cache_01.tmp"}), delete_file({"path": "cache_02.tmp"})
  0.00s  第2轮  权限 放行  delete_file：命中 allow 规则
  0.00s  第2轮  工具完成  delete_file 0ms -> '已删除 cache_01.tmp'
  0.00s  第2轮  权限 放行  delete_file：命中 allow 规则
  0.00s  第2轮  工具完成  delete_file 0ms -> '已删除 cache_02.tmp'
  0.00s  第3轮  请求模型  7 条消息，836 字符
  0.00s  第3轮  模型回复  0ms 120 tokens -> 调用 delete_file({"path": "lab_notes.md"})
  0.00s  第3轮  权限 拒绝  delete_file：被规则 delete_file(*.md) 禁止
  0.00s  第4轮  请求模型  9 条消息，1114 字符
  0.00s  第4轮  模型回复  0ms 120 tokens -> 回答 '清理完成。'
  0.00s  第4轮  结束  状态=done 步数=4 tokens=480

小结：工具调用 4 次，被拒绝 1 次，出错 0 次
```

逐步解释，这正是 todolist 要求你练习的：

| 轮次 | 发生了什么 | 为什么 |
| --- | --- | --- |
| 第 1 轮 | 2 条消息是 system 和 user。模型先列文件 | 它不知道目录里有什么，先观察。`list_files` 是只读工具，默认放行 |
| 第 2 轮 | 消息变成 4 条（多了 assistant 和 tool）。一次调了两个删除 | 这就是并行调用。两个都匹配 `delete_file(*.tmp)`，所以没有打扰用户 |
| 第 3 轮 | 消息变成 7 条（1 条 assistant 加 2 条 tool）。模型想删实验记录 | **这是模型犯的错**。deny 规则先于一切，拦了下来，函数根本没有执行 |
| 第 4 轮 | 模型收到拒绝文字，给出最终回答 | 没有新的 `tool_calls`，循环结束 |

**还要注意最后一句“清理完成”是有问题的。** 它没有提到有个删除被拒绝了。trace 能帮你发现“模型说的”和“实际发生的”不一致，这是只看最终回答发现不了的。Stage 7 会把这种检查变成自动评测。

用真模型运行一次，再用 `trace_view.py` 看，对照上表自己解释一遍。模型可能选择不同的路径，这也是学习的一部分：

```bash
uv run --env-file ../../.env demo.py "清理 workspace 里的临时文件，然后告诉我还剩什么"
```

**再练习读 Claude Code 自己的 trace。** 它把每条消息、工具调用和结果都写进 `~/.claude/projects/` 下的 JSONL 文件。也可以用非交互模式直接输出事件流：

```bash
claude -p "这个目录有几个文件？" --output-format stream-json --verbose
```

对照五大组件想一想：哪些行是模型请求，哪些是工具结果，压缩发生时文件里多了什么。

## 第 5 步：裸循环和 harness 的对比

对比的关键是**控制变量**：同一个任务，同一个模型剧本，同一套工具。这里的剧本模拟一个“过度清理”的模型。用假模型是因为真模型这次犯错、下次可能不犯，得不到确定的对比结果。

新建 `bare_loop.py`（就是 Stage 1 的循环）：

```python
"""对照组：Stage 1 风格的裸循环。工具和 harness 版完全相同，只少了 harness 的那些机制。"""
import json


def bare_loop(task: str, registry, client, model: str = "fake", max_steps: int = 10) -> str:
    messages = [{"role": "user", "content": task}]
    for _ in range(max_steps):
        resp = client.chat.completions.create(model=model, messages=messages, tools=registry.schemas())
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or ""
        for call in msg.tool_calls:
            try:
                result = str(registry.tools[call.function.name].fn(**json.loads(call.function.arguments)))
            except Exception as e:
                result = f"错误：{e}"
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
    return "[停止] 步数用完"
```

新建 `compare.py`：

```python
"""同一个任务、同一个模型剧本，分别交给裸循环和 harness。不联网，运行：uv run compare.py

剧本模拟一个“过度清理”的模型：删完临时文件后，顺手把实验记录也删了。
"""
from bare_loop import bare_loop
from demo import build_gate
from demo_tools import WORKDIR, registry, reset_workspace
from fake_llm import FakeLLM, call
from harness import Harness
from trace_view import main as show_trace

TASK = "清理 workspace 里的临时文件，然后告诉我还剩什么"


def script() -> list:
    return [call("list_files", pattern="*"),
            [call("delete_file", path="cache_01.tmp"), call("delete_file", path="cache_02.tmp")],
            call("delete_file", path="lab_notes.md"),
            "清理完成。"]


def remaining() -> list[str]:
    return sorted(f.name for f in WORKDIR.iterdir())


reset_workspace()
bare_loop(TASK, registry, FakeLLM(script()))
print("裸循环执行后剩下：", remaining())

reset_workspace()
result = Harness(registry, build_gate(ask_user=lambda tool, args: False), client=FakeLLM(script())).run(TASK)
print("harness 执行后剩下：", remaining())
print(f"\n--- harness 的 trace（{result.trace_path}）---")
show_trace(result.trace_path)
reset_workspace()
```

`compare.py` 用到 `fake_llm.py`，先把 Stage 2 的那份拷过来。这是实际运行的结果：

```text
裸循环执行后剩下： []
harness 执行后剩下： ['lab_notes.md']
```

同样的模型错误，裸循环丢了唯一的实验记录，harness 拦了下来。完整对比如下，“场景”这一列都有对应的测试：

| 场景 | 裸循环（19 行） | Harness（约 300 行） |
| --- | --- | --- |
| 模型想删不该删的文件 | 直接删除，无法恢复 | deny 规则拦截，函数没有执行 |
| 只想试跑，不想有副作用 | 做不到 | `--mode plan` 只放行只读工具 |
| 无人值守运行 | 要么全放行，要么卡住等输入 | `dontask` 模式：要确认的操作一律拒绝 |
| 断网或进程崩溃 | 消息全丢，从头再来 | `--resume` 从 JSONL 恢复，接着做 |
| 工具结果 100 KB | 全部塞进上下文，越来越贵 | 超长截断，旧结果省略，再不够就写摘要 |
| 运行后追查原因 | 只能看到最终回答 | trace 记录每次决策、耗时、token |
| 想统一拦截某类操作 | 改每个工具 | 加一个 hook |
| 模型调用次数和时间 | 只限步数 | 步数和总时长都限制，状态写明原因 |

但 harness 不是免费的。**简单的一次性任务用裸循环就够了**：只有读操作、不需要恢复、不怕失败的脚本，300 行的 harness 只会增加负担。判断标准是**出错的代价**和**运行时长**：能造成不可逆后果的工具，或者要运行很久的任务，才值得加这些机制。

### 测试

<details>
<summary>fake_llm.py（和 Stage 2 相同，点击展开）</summary>

```python
"""按剧本回答的假模型：不联网、不花钱、每次结果都一样，专门用来测 agent 的逻辑。

剧本里每一项代表模型的一次回复：字符串是最终回答，call(...) 或它组成的列表是工具调用。
"""
import copy
import json

from openai.types.chat import ChatCompletion


def call(name: str, /, **args) -> dict:
    return {"name": name, "args": args}


class FakeLLM:
    def __init__(self, script: list):
        self.script = list(script)
        self.requests = []                     # 记下每次请求，测试里可以检查模型看到了什么
        self.chat = self
        self.completions = self

    def create(self, **kwargs) -> ChatCompletion:
        self.requests.append(copy.deepcopy(kwargs))
        step = self.script.pop(0)
        if isinstance(step, Exception):        # 剧本里放异常 = 模拟网络错误、限流
            raise step
        calls = [step] if isinstance(step, dict) else step if isinstance(step, list) else []
        message = {"role": "assistant", "content": step if isinstance(step, str) else ""}
        if calls:
            message["tool_calls"] = [
                {"id": f"call_{len(self.requests)}_{i}", "type": "function",
                 "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}}
                for i, c in enumerate(calls)]
        return ChatCompletion.model_validate({
            "id": "fake", "object": "chat.completion", "created": 0, "model": "fake",
            "choices": [{"index": 0, "message": message,
                         "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})
```

</details>

<details>
<summary>test_harness.py（点击展开）</summary>

```python
"""运行：uv run pytest test_harness.py -q"""
import json

import pytest

import demo_tools
from demo import block_secrets, build_gate
from fake_llm import FakeLLM, call
from harness import Harness, PermissionGate, SessionStore, ToolRegistry, micro_compact, safe_cut


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(demo_tools, "WORKDIR", (tmp_path / "workspace").resolve())
    demo_tools.reset_workspace()


def make(script, gate=None, **kwargs):
    gate = gate or build_gate(ask_user=lambda tool, args: False)
    return Harness(demo_tools.registry, gate, client=FakeLLM(script), **kwargs)


def events(result):
    with open(result.trace_path, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_registry_builds_schema_from_signature():
    reg = ToolRegistry()

    @reg.tool(risk="write")
    def add(a: int, note: str = "") -> str:
        """加法"""
        return str(a)

    fn = reg.schemas()[0]["function"]
    assert fn["description"] == "加法"
    assert fn["parameters"]["properties"]["a"]["type"] == "integer"
    assert fn["parameters"]["required"] == ["a"]


def test_rule_order_is_deny_then_ask_then_allow():
    tool = demo_tools.registry.tools["delete_file"]
    asked = []
    gate = PermissionGate(deny=["delete_file(*.md)"], ask=["delete_file(keep*)"], allow=["delete_file(*)"],
                          ask_user=lambda t, a: asked.append(a) or False)
    assert not gate.check(tool, {"path": "a.md"}).allowed                # deny 优先于 allow
    assert not gate.check(tool, {"path": "keep.tmp"}).allowed and asked  # ask 优先于 allow
    assert gate.check(tool, {"path": "x.tmp"}).allowed                   # 最后才看 allow


def test_read_tools_pass_and_plan_mode_blocks_writes():
    read, write = demo_tools.registry.tools["read_file"], demo_tools.registry.tools["write_file"]
    plan = PermissionGate(mode="plan")
    assert plan.check(read, {"path": "a"}).allowed
    assert not plan.check(write, {"path": "a"}).allowed
    assert not PermissionGate(mode="dontask").check(write, {"path": "a"}).allowed


def test_denied_action_does_not_run_and_is_reported_to_model():
    r = make([call("delete_file", path="lab_notes.md"), "删不了，已说明"]).run("删除")
    assert (demo_tools.WORKDIR / "lab_notes.md").exists()
    denied = [e for e in events(r) if e["kind"] == "permission" and not e["allowed"]]
    assert "delete_file(*.md)" in denied[0]["reason"]
    assert r.status == "done"


def test_hook_can_block_before_permission_check():
    h = make([call("read_file", path="secret.txt"), "好"], pre_tool_hooks=[block_secrets])
    r = h.run("读")
    assert "hook" in [e for e in events(r) if e["kind"] == "permission"][0]["reason"]


def test_post_hook_rewrites_and_long_output_is_truncated():
    h = make([call("read_file", path="lab_notes.md"), "好"],
             post_tool_hooks=[lambda t, a, res: res + "x" * 9000], tool_output_limit=100)
    h.run("读")
    tool_msg = [m for m in h.client.requests[-1]["messages"] if m["role"] == "tool"][0]
    assert "已截断" in tool_msg["content"] and len(tool_msg["content"]) < 200


def test_bad_tool_name_args_and_tool_exception_are_fed_back():
    script = [call("nope"), call("read_file", path="missing.md"), call("read_file", wrong="x"), "结束"]
    h = make(script)
    r = h.run("读")
    tool_msgs = [m["content"] for m in h.client.requests[-1]["messages"] if m["role"] == "tool"]
    assert "没有工具" in tool_msgs[0] and "FileNotFoundError" in tool_msgs[1] and "TypeError" in tool_msgs[2]
    assert r.status == "done"


def test_session_can_be_resumed_after_a_crash():
    crash = make([call("list_files", pattern="*"), RuntimeError("断网了")])
    r1 = crash.run("第一个任务")
    assert r1.status == "model_error"
    h = make(["接着做完了"])
    r2 = h.run("继续", session_id=r1.session_id)
    sent = h.client.requests[0]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "tool", "user"]
    assert r2.session_id == r1.session_id


def test_micro_compact_keeps_recent_and_pairs():
    msgs = [{"role": "tool", "tool_call_id": str(i), "content": "x" * 1000} for i in range(5)]
    assert micro_compact(msgs, keep_recent=2) == 3
    assert msgs[0]["tool_call_id"] == "0" and "已省略" in msgs[0]["content"] and msgs[-1]["content"] == "x" * 1000


def test_safe_cut_never_starts_on_a_tool_message():
    msgs = [{"role": "system"}, {"role": "user"}, {"role": "assistant"}, {"role": "tool"}, {"role": "tool"},
            {"role": "assistant"}]
    assert msgs[safe_cut(msgs, keep_last=2)]["role"] != "tool"
    assert safe_cut(msgs, keep_last=2) == 5


def _big_file():
    demo_tools.WORKDIR.joinpath("big.md").write_text("y" * 3000, encoding="utf-8")


def test_summary_compaction_replaces_old_turns_and_survives_resume():
    _big_file()
    # 第 2 轮开始前上下文已超限，所以剧本第 2 项会被用来写摘要，第 3 项才是下一轮回复
    h = make([call("read_file", path="big.md"), "摘要：用户想读 big.md", "完成"], compact_at=2500, keep_last=2)
    r = h.run("读 big.md")
    assert r.status == "done"
    assert "summary" in [e["level"] for e in events(r) if e["kind"] == "compact"]
    restored = SessionStore(r.session_id).load()
    assert restored[0]["role"] == "system" and "摘要：用户想读" in restored[1]["content"]
    assert restored[2]["tool_calls"][0]["id"] == restored[3]["tool_call_id"]     # 配对完好
    assert restored[-1]["content"] == "完成"


def test_compaction_failure_does_not_kill_the_run():
    _big_file()
    script = [call("read_file", path="big.md"), RuntimeError("摘要失败"), "完成"]
    r = make(script, compact_at=2500, keep_last=2).run("读")
    assert r.status == "done"
    assert any(e["kind"] == "compact_failed" for e in events(r))


def test_limits_are_reported():
    r = make([call("list_files", pattern="*") for _ in range(3)], max_steps=2).run("读")
    assert r.status == "max_steps" and r.steps == 2
    r = make([call("list_files", pattern="*")], total_timeout=-1).run("读")
    assert r.status == "timeout"
```

</details>

运行后应看到 `13 passed`：

```bash
uv run pytest test_harness.py -q
```

## 产出：一个可交付的 demo

Hub 要求这一关交付 README、运行步骤、示例输入输出和失败记录。把 `stage3/` 目录整理好，再加上下面两个文件。

`README.md` 模板，尖括号的地方换成你自己的内容：

````markdown
# Nano Harness

一个可调试的迷你 agent harness，包含权限闸门、hooks、会话恢复、上下文压缩和 trace。

## 运行

```bash
uv run --env-file ../../.env demo.py "清理 workspace 里的临时文件，然后告诉我还剩什么"
uv run trace_view.py .traces/<运行后打印的文件名>
uv run pytest test_harness.py -q
```

## 示例

输入：`清理 workspace 里的临时文件，然后告诉我还剩什么`
输出：<贴一次真实运行的回答>
Trace：<贴 trace_view 的输出>

## 组件

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| Agent loop | `Harness.run` | <你的理解> |
| ...... | | |

## 已知限制

- <例如：工具函数在当前进程内运行，没有真正的沙箱>

## 失败记录

见 FAILURES.md。
````

`FAILURES.md` 比 README 更有价值。**每次失败都记下来：**

```markdown
| 日期 | 现象 | trace 里看到什么 | 根因 | 修复 | 回归测试 |
| --- | --- | --- | --- | --- | --- |
| 2026-11-03 | 用 --resume 恢复时 API 报 400 | 恢复后第一条请求里 tool 消息落单 | 压缩时从 tool 消息处切开 | 加 safe_cut | test_safe_cut_never_starts_on_a_tool_message |
```

至少主动制造并记录这三次失败：

1. 把 `ask_user` 配成总是拒绝，看模型怎么反应。它会绕路吗？
2. 运行中按 `Ctrl+C`，再用 `--resume` 恢复。
3. 把 `compact_at` 调到很小，观察压缩对最终回答质量的影响。

## 自检清单、常见坑与下一步

下面五项都能做到，Stage 3 就算完成了：

- [ ] 拿到一个陌生 agent 项目，能在 10 分钟内定位五大组件。
- [ ] 跑通 s01 和 s02，并加了一个自己的工具。
- [ ] 能说出 deny、ask、allow 的匹配顺序，以及为什么要这样排。
- [ ] 能逐行解释一次真实 trace，并指出“模型说的”和“实际发生的”有没有差异。
- [ ] 完成了对比，能说清什么场景不值得用 harness。`FAILURES.md` 至少有 3 条记录。

### 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 恢复会话后报 400 | 会话里有落单的 `tool` 消息，或者崩溃时有调用没回结果 | 恢复时检查配对，给没有结果的调用补一条“已中断” |
| 每一轮都在压缩 | 保留段比 `compact_at` 还大 | 调大 `compact_at`，或减小 `keep_last` 和 `tool_output_limit` |
| 压缩后模型忘了任务 | 摘要丢了原始目标 | 摘要提示词要求保留原话；重要规则放 system prompt |
| 权限规则不生效 | 规则只匹配第一个参数，或参数顺序不是预期的 | 检查 `_match`；真实项目应按参数名匹配 |
| 被拒绝后模型换工具绕路 | 工具之间能力重叠，比如有 `bash` 又有 `delete_file` | 拒绝信息写清楚；限制危险的通用工具 |
| trace 文件里有敏感信息 | 工具参数和结果都写了进去 | 写入前脱敏，并把 `.traces/` 加入 `.gitignore` |
| `learn-claude-code` 换模型后报错 | 不同服务的 Anthropic 兼容程度不一样 | 查该服务的文档，或者先用项目默认配置 |

把这些也加进 `.gitignore`：

```bash
printf '.traces/\n' >> .gitignore
```

### 进阶练习

1. 把恢复时的配对检查补上：崩溃时如果有调用没回结果，自动补一条“已中断”的 `tool` 消息。
2. 给 `PermissionGate` 加“本次会话都允许”，对照 Claude Code 权限提示里的选项。
3. 把权限规则放进 `settings.json`，分用户级和项目级，体会 Claude Code 的配置分层。
4. 读 s15 的 `permission_hook` 和 `compact_history`，看看和你写的差在哪。
5. 用 [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) 重写主循环，对比“显式状态图”和“while 循环”。

下一步：Stage 4 用这些机制协调多个 agent。你会发现 subagent 的核心就是“一份新的 `messages`”，也就是上下文隔离。

### 资料来源

- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)
- [Claude Code Permissions](https://code.claude.com/docs/en/permissions)
- [Claude Code 非交互模式](https://code.claude.com/docs/en/headless)
- [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)
- [claw0](https://github.com/shareAI-lab/claw0)
- [hello-agents](https://github.com/datawhalechina/hello-agents)
- [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)
- [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Dive into Claude Code](https://arxiv.org/abs/2604.14228)
