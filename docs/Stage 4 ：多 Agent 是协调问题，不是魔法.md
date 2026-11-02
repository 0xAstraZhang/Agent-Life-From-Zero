## 导读：学完你会得到什么

跟着这份教程敲完，你会得到一条约 300 行的写作流水线：

```text
plan → research → write → review ──通过──→ 完成
                            │  ↑
                         不通过 │
                            ↓  │
                          revise     （修改 2 轮仍不过，或同样的问题反复出现 → 转交给人）
```

输入一个任务，五个 agent 依次接力，最后输出带来源的简报。你还会得到同一流程的 LangGraph 版本、Claude Code subagents 版本，以及一个用来对比的单 agent 版本。

**先说最重要的结论：大多数任务不需要多 agent。** 多 agent 更贵、更慢、更难调试，只有当它解决了单 agent 解决不了的问题时才值得用。第 5 步会讲怎么判断。

核心只有一句话：**流程由代码决定，agent 之间只交接结构化产物，不随便聊天。**

学习路线和 todolist 一一对应：

1. 角色：planner、executor、reviewer、critic、router 各做什么。
2. 编排：用 supervisor 或 graph 管理，而不是让 agent 自由对话。
3. 边界：职责、输入输出 schema、停止条件。
4. 故障：循环、争论、任务漂移、上下文膨胀。
5. 判断：什么时候单 agent 更好。

前置知识：做完 Stage 1。做过 Stage 3 更好，这里的 subagent 就是“一份新的 `messages`”。

## 第 0 步：环境准备

```bash
uv add openai pydantic langgraph
```

```bash
uv add --dev pytest
```

```bash
mkdir -p agent-life-from-zero/stage4
```

`.env` 沿用前面的配置。如果还没有，看 Stage 2 的第 0 步，并确认 `.env` 已经加入 `.gitignore`。

## 第 1 步：常见角色

这些名字只是职责的简称，不要把它们当成扮演游戏。一个角色值不值得存在，看的是它有没有**不同的上下文、不同的权限，或者不同的工具**。

| 角色 | 做什么 | 不做什么 | 本教程 |
| --- | --- | --- | --- |
| Planner | 拆解任务，定下验收标准 | 不查资料，不写正文 | `PLANNER` |
| Executor | 动手做事，比如查资料、写稿 | 不评价自己的结果 | `researcher`、`WRITER`、`REVISER` |
| Reviewer | 对照标准检查，给出通过或问题清单 | 不改稿，不提标准之外的要求 | `REVIEWER` |
| Critic | 主动挑错，提反对意见 | 不负责给出最终结论 | 没用，可以当练习 |
| Router | 判断该交给谁 | 不做具体工作 | 代码 `route_after_review` |

这里的 Router 不是 agent，而是代码。**能用 `if` 判断的，就不要花钱问模型。**

## 第 2 步：用 Supervisor 或 Graph 管理

多 agent 主要有三种组织方式：

| 方式 | 谁决定下一步 | 优点 | 风险 |
| --- | --- | --- | --- |
| 固定流水线或图 | 代码 | 可预测、好测试、好调试 | 流程不够灵活 |
| Supervisor agent | 一个主 agent 动态分派 | 能应对事先想不到的情况 | 更贵，主 agent 可能决策失误 |
| 群聊，互相对话 | 谁也不确定 | 看起来很“智能” | 跑题、循环、停不下来 |

**从固定流程开始。** 只有当下一步确实要看具体内容才能决定时，才用 supervisor。“群聊”几乎总是最差的选择，这也是 Hub 建议不要重押老式 crew 和角色扮演框架的原因。

这里用最朴素的方式实现状态图。每个节点是一个函数，做完一件事后返回下一个节点的名字：

```python
NODES = {"plan": plan_node, "research": research_node, "write": write_node,
         "review": review_node, "revise": revise_node}

def run_pipeline(brief, ...):
    state, ctx, node = State(brief), make_context(...), "plan"
    while node != "end":
        state.trail.append(node)
        node = NODES[node](state, ctx)
```

这就是图的全部原理。LangGraph 这类框架额外提供的，主要是持久化、恢复执行和人工中断，后面会讲。

## 第 3 步：定义职责边界、Schema 和停止条件

每个 agent 上线前，都要能回答这张表里的问题：

| 问题 | 为什么重要 | 以 reviewer 为例 |
| --- | --- | --- |
| 它负责什么、不负责什么？ | 防止两个 agent 做同一件事，或者都不做 | 只检查，不改稿 |
| 输入是什么？ | 决定它的上下文 | `brief`、`acceptance`、`notes`、`draft` |
| 输出的 schema 是什么？ | 代码能解析，才能路由 | `verdict`、`on_brief`、`issues` |
| 有哪些工具？ | 权限最小化 | 没有工具，只能读给它的内容 |
| 什么时候算结束？ | 防止无限循环 | 输出合法 JSON；最多 4 步 |
| 失败了怎么办？ | 不能让流水线卡死 | 抛 `AgentError`，整体标记为 `failed` |

这里用 pydantic 定义 schema。比手写 `isinstance` 省事，错误信息也详细，可以直接发回给模型让它改正：

```python
class Review(BaseModel):
    verdict: Literal["pass", "revise"]
    on_brief: bool = Field(description="成稿是否仍在回答原始任务")
    issues: list[Issue] = Field(default_factory=list)
```

**不要完全相信模型填的字段。** 模型可能写 `verdict: "pass"`，同时列出一个 `blocker` 问题。所以路由时看的是事实，不是结论：

```python
def route_after_review(s):
    blockers = {i.problem.strip() for i in s.review.issues if i.severity == "blocker"}
    if not blockers and s.review.on_brief:          # 没有用到 s.review.verdict
        s.status = "done"
        return "end"
    ...
```

## 第 4 步：处理四种典型故障

| 故障 | 表现 | 代码怎么防 |
| --- | --- | --- |
| 循环 | 审稿和修改一直来回 | `MAX_REVISIONS` 限制轮数，`MAX_TOTAL_TOKENS` 限制总花费 |
| 争论 | 审稿人反复提同一个问题，修改者改不掉 | 连续两轮问题相同，就立即转交给人 |
| 任务漂移 | 写着写着偏离了原始任务 | `brief` 和 `acceptance` 始终原样传递；审稿人判断 `on_brief`；不许提标准之外的要求 |
| 上下文膨胀 | 每个 agent 都带着全部历史，越来越贵 | 只交接产物，不交接对话过程 |

三点补充：

**转交给人也是一种结果。** 流水线有三种结束状态：`done`、`needs_human`、`failed`。`needs_human` 会同时给出当前草稿和未解决的问题，不会悄悄输出一份没通过的稿子，也不会把已有成果丢掉。

**审稿人不能自己提高标准。** 如果它每轮都能提新要求，修改就永远结束不了。所以验收标准在开头由 planner 定下，审稿人只能对照它们。这也是 planner 这一步存在的主要理由。

**上下文隔离的效果要亲眼看看。** 下面是假模型跑一遍带一次修改的流程得到的真实记录（token 数是假模型的固定值，字符数是真实计算的）：

```text
plan → research → write → review → revise → review  done

agent             发送字符    tokens
planner            616       120
researcher         933       120
researcher        1559       120
writer             721       120
reviewer          1333       120
reviser            833       120
reviewer          1360       120
合计 7 次调用，840 tokens
```

注意 `writer` 只发了 721 字符，比研究员第二次请求的 1559 字符还少，因为它看不到搜索过程，只看到整理好的事实。这就是隔离的价值。如果所有 agent 共用一份历史，最后的 reviewer 要带上前面所有的内容。

### 能用代码查的，就不要让 agent 查

流水线里有两道代码检查，都不花 token：

- `research_node` 丢掉来源不是工具真实返回过的事实，防止研究员编造来源。
- `check_refs` 检查稿件里的 `[F9]` 是否存在。查出问题就直接进入修改，省下一次审稿调用。

`test_bad_reference_skips_reviewer_call` 这个测试验证了这一点。

## 第 5 步：什么时候单 agent 更好

Anthropic 在介绍自家多 agent 研究系统时给过两组数字：内部研究评测中，多 agent 比单 agent 表现好 90.2%；但多 agent 的 token 用量约是普通对话的 15 倍，而单 agent 约是 4 倍。所以它只适合**价值足够高、能拆成多个独立方向**的任务。Cognition 也写过一篇观点相反的文章 [Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents)，主要担心并行 agent 之间上下文不共享，导致决策互相冲突。两篇都值得读。

| 选单 agent，如果…… | 选多 agent，如果…… |
| --- | --- |
| 任务步骤之间紧密相关，后一步要用前一步的细节 | 子任务相互独立，可以并行 |
| 需要频繁和用户来回确认 | 中间过程很长，主对话不需要看 |
| 上下文放得下 | 单个上下文放不下 |
| 更在意速度和成本 | 需要限制权限，比如审稿人不能改文件 |
| 还没有评测，不知道是否真的有提升 | 已经有评测，数据证明多 agent 更好 |

**用数据决定，不要靠感觉。** 流水线带了 `--single` 对照模式，下面两条命令用的是同一个任务：

```bash
uv run --env-file ../../.env multi_agent.py "写一份样本B生长情况的简报"
```

```bash
uv run --env-file ../../.env multi_agent.py "写一份样本B生长情况的简报" --single
```

用 5 个不同的任务各跑两次，填这张表。这个结果只属于你的模型和任务，别人的结论替代不了：

| 任务 | 方式 | 调用次数 | tokens | 耗时 | 事实全对？ | 符合验收标准？ |
| --- | --- | --- | --- | --- | --- | --- |
| 样本B简报 | 单 agent | | | | | |
| 样本B简报 | 流水线 | | | | | |

这个流水线的任务其实很小，单 agent 很可能表现得一样好。**如果你的数据也是这样，那就是正确的结论。** 学到“何时不该用”，和学会怎么做同样重要。

## 完整代码

新建 `multi_agent.py`：

```python
"""Stage 4 多 agent 写作流水线：plan → research → write → review → revise → review …

每个 agent 都有独立的 messages（上下文隔离）和固定的输出 schema。流程由普通 Python 代码决定，
agent 之间不直接对话，只通过 State 里的结构化产物交接。

运行：uv run --env-file ../../.env multi_agent.py "写一份样本B生长情况的简报"
对照：加 --single，用单个 agent 完成同样的任务
"""
import argparse
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

WORKDIR = Path("workspace").resolve()
MAX_REVISIONS = 2          # 最多修改几轮
MAX_TOTAL_TOKENS = 30000   # 整条流水线的 token 上限


# ---------- 交接产物：每个 agent 的输入输出都是固定结构 ----------
class Plan(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=5, description="要查清楚的问题")
    acceptance: list[str] = Field(min_length=1, max_length=5, description="成稿必须满足的验收标准")


class Fact(BaseModel):
    id: str = Field(description="事实编号，如 F1")
    claim: str
    source: str = Field(description="工具返回的来源位置，如 lab_notes.md:3")


class Notes(BaseModel):
    facts: list[Fact]
    gaps: list[str] = Field(default_factory=list, description="没查到的问题")


class Draft(BaseModel):
    title: str
    body: str = Field(description="Markdown 正文，每个事实后标 [F编号]")


class Issue(BaseModel):
    severity: Literal["blocker", "minor"]
    problem: str
    fix: str


class Review(BaseModel):
    verdict: Literal["pass", "revise"]
    on_brief: bool = Field(description="成稿是否仍在回答原始任务")
    issues: list[Issue] = Field(default_factory=list)


# ---------- agent 定义：职责写进 system，工具决定它能做什么 ----------
class AgentError(Exception):
    pass


class BudgetExceeded(Exception):
    pass


@dataclass
class AgentSpec:
    name: str
    system: str
    output: type[BaseModel]
    tools: dict[str, Callable] = field(default_factory=dict)
    max_steps: int = 4


@dataclass
class Context:
    client: object
    model: str
    max_tokens: int = MAX_TOTAL_TOKENS
    tokens: int = 0
    calls: list = field(default_factory=list)      # 每次模型调用的记录：谁、发了多少字符、用了多少 token
    seen_sources: set = field(default_factory=set)  # 工具真实返回过的来源

    def create(self, agent: str, messages: list[dict], **kwargs):
        if self.tokens >= self.max_tokens:
            raise BudgetExceeded(f"已用 {self.tokens} tokens，超过上限 {self.max_tokens}")
        resp = self.client.chat.completions.create(model=self.model, messages=messages, **kwargs)
        used = resp.usage.total_tokens if resp.usage else 0
        self.tokens += used
        self.calls.append({"agent": agent, "chars": sum(len(json.dumps(m, ensure_ascii=False)) for m in messages),
                           "tokens": used})
        return resp


def _extract_json(text: str) -> str:
    """模型有时会把 JSON 包在代码块里，或者在前后加几句话。"""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("回复里没有 JSON 对象")
    return text[start:end + 1]


def _schema(tools: dict[str, Callable]) -> list[dict]:
    return [{"type": "function", "function": {
        "name": name, "description": fn.__doc__,
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}
        for name, fn in tools.items()]


def call_agent(spec: AgentSpec, payload: dict, ctx: Context) -> BaseModel:
    """运行一个 agent。它只看得到 payload，看不到其他 agent 的对话过程。"""
    system = (f"{spec.system}\n\n最终只输出一个符合下面 JSON Schema 的对象，不要输出其他文字：\n"
              f"{json.dumps(spec.output.model_json_schema(), ensure_ascii=False)}")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    kwargs = {"tools": _schema(spec.tools)} if spec.tools else {"response_format": {"type": "json_object"}}
    for _ in range(spec.max_steps):
        msg = ctx.create(spec.name, messages, **kwargs).choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if msg.tool_calls:
            for tc in msg.tool_calls:
                try:
                    result = spec.tools[tc.function.name](**json.loads(tc.function.arguments or "{}"))
                except Exception as e:
                    result = f"错误：{type(e).__name__}: {e}"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(result)})
            continue
        try:
            return spec.output.model_validate_json(_extract_json(msg.content or ""))
        except (ValidationError, ValueError) as e:      # 不符合 schema 就打回去，把错误告诉它
            messages.append({"role": "user", "content": f"输出不符合 schema，请改正后重新输出：{e}"})
    raise AgentError(f"{spec.name} 用完 {spec.max_steps} 步，仍没有给出合法结果")


# ---------- 研究员的工具：这里用本地关键词搜索，可以换成 Stage 2 的工具箱 ----------
def make_search(ctx: Context) -> Callable:
    def search_notes(query: str) -> str:
        """在本地资料里搜索关键词，返回“文件:行号: 内容”"""
        hits = []
        for f in sorted(WORKDIR.rglob("*.md")):
            for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if query.lower() in line.lower():
                    source = f"{f.relative_to(WORKDIR)}:{no}"
                    ctx.seen_sources.add(source)
                    hits.append(f"{source}: {line.strip()}")
        return "\n".join(hits[:20]) or f"没有找到“{query}”，换个关键词再试。"
    return search_notes


PLANNER = AgentSpec("planner", "你是规划员。把任务拆成要查清的问题，并定下成稿的验收标准。不写正文，不查资料。", Plan)
RESEARCHER = "你是研究员。用工具查资料，回答 questions。只记录查到的事实，source 必须照抄工具返回的“文件:行号”。查不到的放进 gaps。不写文章。"
WRITER = AgentSpec("writer", "你是写作者。只根据 notes 里的事实写作，不得增加其他事实，每个事实后标 [F编号]。notes.gaps 里的内容要明确说明没查到。", Draft)
REVIEWER = AgentSpec("reviewer", "你是审稿人。只对照 brief、acceptance 和 notes 检查 draft，不要提出验收标准之外的新要求。on_brief 判断成稿是否还在回答原始任务。问题分为 blocker（必须改）和 minor（可不改）。", Review)
REVISER = AgentSpec("reviser", "你是修订者。根据 issues 修改 draft，只改有问题的部分，其余保持不变。仍然只能使用 notes 里的事实。", Draft)


# ---------- 状态和流程：一个节点做一件事，然后返回下一个节点的名字 ----------
@dataclass
class State:
    brief: str
    plan: Plan | None = None
    notes: Notes | None = None
    draft: Draft | None = None
    review: Review | None = None
    revisions: int = 0
    last_problems: set = field(default_factory=set)
    trail: list[str] = field(default_factory=list)
    status: str = "running"     # done / needs_human / failed
    reason: str = ""


def check_refs(draft: Draft, notes: Notes) -> list[Issue]:
    """能用代码查的，就不要花钱让 agent 查。"""
    ids = {f.id for f in notes.facts}
    used = set(re.findall(r"\[(F\d+)\]", draft.body))
    issues = [Issue(severity="blocker", problem=f"引用了不存在的事实 [{i}]", fix="删除或改成 notes 里的编号")
              for i in sorted(used - ids)]
    if ids and not used:
        issues.append(Issue(severity="blocker", problem="正文没有引用任何事实", fix="每个事实后标 [F编号]"))
    return issues


def plan_node(s: State, ctx: Context) -> str:
    s.plan = call_agent(PLANNER, {"brief": s.brief}, ctx)
    return "research"


def research_node(s: State, ctx: Context) -> str:
    spec = AgentSpec("researcher", RESEARCHER, Notes, tools={"search_notes": make_search(ctx)}, max_steps=6)
    notes = call_agent(spec, {"brief": s.brief, "questions": s.plan.questions}, ctx)
    verified = [f for f in notes.facts if f.source in ctx.seen_sources]        # 丢掉来源不是工具返回的事实
    dropped = [f.claim for f in notes.facts if f.source not in ctx.seen_sources]
    s.notes = Notes(facts=verified, gaps=notes.gaps + [f"来源无法核实，已删除：{c}" for c in dropped])
    return "write"


def write_node(s: State, ctx: Context) -> str:
    s.draft = call_agent(WRITER, {"brief": s.brief, "acceptance": s.plan.acceptance,
                                  "notes": s.notes.model_dump()}, ctx)
    return "review"


def review_node(s: State, ctx: Context) -> str:
    issues = check_refs(s.draft, s.notes)
    if issues:                                  # 代码已经查出硬伤，省下这次审稿调用
        s.review = Review(verdict="revise", on_brief=True, issues=issues)
    else:
        s.review = call_agent(REVIEWER, {"brief": s.brief, "acceptance": s.plan.acceptance,
                                         "notes": s.notes.model_dump(), "draft": s.draft.model_dump()}, ctx)
    return route_after_review(s)


def route_after_review(s: State) -> str:
    """路由由代码决定，不信 verdict 字段本身：只要还有 blocker，或者跑题了，就不算通过。"""
    blockers = {i.problem.strip() for i in s.review.issues if i.severity == "blocker"}
    if not blockers and s.review.on_brief:
        s.status = "done"
        return "end"
    if blockers and blockers == s.last_problems:
        s.status, s.reason = "needs_human", "连续两轮是同样的问题，继续修改没有意义"
        return "end"
    if s.revisions >= MAX_REVISIONS:
        s.status, s.reason = "needs_human", f"修改 {MAX_REVISIONS} 轮后仍未通过"
        return "end"
    s.last_problems = blockers
    return "revise"


def revise_node(s: State, ctx: Context) -> str:
    s.revisions += 1
    s.draft = call_agent(REVISER, {"brief": s.brief, "notes": s.notes.model_dump(), "draft": s.draft.model_dump(),
                                   "issues": [i.model_dump() for i in s.review.issues]}, ctx)
    return "review"


NODES = {"plan": plan_node, "research": research_node, "write": write_node,
         "review": review_node, "revise": revise_node}


def make_context(client=None, max_tokens: int = MAX_TOTAL_TOKENS) -> Context:
    client = client or OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"),
                              timeout=60, max_retries=2)
    return Context(client, os.getenv("LLM_MODEL", "deepseek-flash"), max_tokens)


def run_pipeline(brief: str, client=None, max_tokens: int = MAX_TOTAL_TOKENS) -> tuple[State, Context]:
    state, ctx, node = State(brief), make_context(client, max_tokens), "plan"
    try:
        while node != "end":
            state.trail.append(node)
            node = NODES[node](state, ctx)
    except (AgentError, BudgetExceeded) as e:
        state.status, state.reason = "failed", str(e)
    return state, ctx


# ---------- 对照组：一个 agent 从头做到尾 ----------
SINGLE = ("你是研究写作助手。先用 search_notes 查资料，只根据查到的内容写简报，每个事实后标 [文件:行号]，"
          "写完自己检查一遍。查不到的明确说明没查到。")


def run_single(brief: str, client=None, max_steps: int = 10) -> tuple[str, Context]:
    ctx = make_context(client)
    tools = {"search_notes": make_search(ctx)}
    messages = [{"role": "system", "content": SINGLE}, {"role": "user", "content": brief}]
    for _ in range(max_steps):
        msg = ctx.create("single", messages, tools=_schema(tools)).choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            return msg.content or "", ctx
        for tc in msg.tool_calls:
            result = tools[tc.function.name](**json.loads(tc.function.arguments or "{}"))
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    return "[停止] 步数用完", ctx


DEMO_NOTES = {
    "lab_notes.md": "# 实验记录\n2026-10-30 测 OD600：样本A=0.82，样本B=1.37，样本C=0.95。\n2026-10-31 样本B 转接到新培养基，待观察。\n",
    "protocol.md": "# 实验方案\nOD600 超过 1.0 时读数不再与菌浓度成线性关系，需要稀释后重新测量。\n",
}


def init_demo() -> None:
    WORKDIR.mkdir(exist_ok=True)
    for name, text in DEMO_NOTES.items():
        if not (WORKDIR / name).exists():
            (WORKDIR / name).write_text(text, encoding="utf-8")


def print_usage(ctx: Context) -> None:
    print(f"\n{'agent':<12}{'发送字符':>10}{'tokens':>10}")
    for c in ctx.calls:
        print(f"{c['agent']:<12}{c['chars']:>10}{c['tokens']:>10}")
    print(f"合计 {len(ctx.calls)} 次调用，{ctx.tokens} tokens")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("brief")
    parser.add_argument("--single", action="store_true", help="用单个 agent 完成，方便对比")
    args = parser.parse_args()
    init_demo()
    if args.single:
        text, context = run_single(args.brief)
        print(text)
    else:
        result, context = run_pipeline(args.brief)
        print("路径：", " → ".join(result.trail))
        print(f"状态：{result.status} {result.reason}")
        if result.draft:
            print(f"\n# {result.draft.title}\n\n{result.draft.body}")
        if result.notes:
            print("\n## 来源\n" + "\n".join(f"[{f.id}] {f.source}" for f in result.notes.facts))
        if result.status == "needs_human" and result.review:
            print("\n## 需要你来判断的问题\n" + "\n".join(f"- {i.problem}" for i in result.review.issues))
    print_usage(context)
```

用这些场景验收：

| 输入或操作 | 应该看到的行为 | 验证的是 |
| --- | --- | --- |
| `写一份样本B生长情况的简报` | 走完流水线，正文有 `[F1]`，末尾有来源 | 基本流程 |
| `写一份样本B的简报，要包含样本Z的数据` | `gaps` 里写明查不到样本Z，正文不编造 | 诚实 |
| `MAX_REVISIONS` 改成 0，再用一个苛刻的任务 | 第一次审稿不通过就停止，状态 `needs_human` | 循环上限 |
| `MAX_TOTAL_TOKENS` 改成 500 | 状态 `failed`，原因写明超预算 | 预算上限 |
| 看 `print_usage` 的输出 | 除研究员外，每个 agent 都只调用一两次 | 上下文隔离 |

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
<summary>test_multi_agent.py（点击展开）</summary>

```python
"""运行：uv run pytest test_multi_agent.py -q"""
import json

import pytest

import multi_agent as ma
from fake_llm import FakeLLM, call

PLAN = json.dumps({"questions": ["样本B的OD600是多少"], "acceptance": ["给出样本B的读数"]}, ensure_ascii=False)
NOTES = json.dumps({"facts": [{"id": "F1", "claim": "样本B=1.37", "source": "lab_notes.md:2"}], "gaps": []}, ensure_ascii=False)
DRAFT = json.dumps({"title": "样本B简报", "body": "样本B的OD600是1.37 [F1]。"}, ensure_ascii=False)
PASS = json.dumps({"verdict": "pass", "on_brief": True, "issues": []})


def review_with(problem: str, verdict: str = "revise", severity: str = "blocker") -> str:
    return json.dumps({"verdict": verdict, "on_brief": True,
                       "issues": [{"severity": severity, "problem": problem, "fix": "改"}]}, ensure_ascii=False)


SEARCH = call("search_notes", query="OD600")
START = [PLAN, SEARCH, NOTES, DRAFT]     # 规划、搜索、记笔记、写稿


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ma, "WORKDIR", (tmp_path / "workspace").resolve())
    ma.init_demo()


def roles(client):
    return [r["messages"][0]["content"][:6] for r in client.requests]


def test_happy_path():
    state, ctx = ma.run_pipeline("写简报", FakeLLM(START + [PASS]))
    assert state.trail == ["plan", "research", "write", "review"]
    assert state.status == "done" and "1.37" in state.draft.body
    assert [c["agent"] for c in ctx.calls] == ["planner", "researcher", "researcher", "writer", "reviewer"]


def test_revision_loop_then_pass():
    state, _ = ma.run_pipeline("写简报", FakeLLM(START + [review_with("缺结论"), DRAFT, PASS]))
    assert state.trail == ["plan", "research", "write", "review", "revise", "review"]
    assert state.status == "done" and state.revisions == 1


def test_stops_after_max_revisions_and_hands_to_human():
    script = START + [review_with("问题1"), DRAFT, review_with("问题2"), DRAFT, review_with("问题3")]
    state, _ = ma.run_pipeline("写简报", FakeLLM(script))
    assert state.status == "needs_human" and state.revisions == ma.MAX_REVISIONS
    assert state.draft is not None and state.review.issues            # 草稿和问题都留给人看


def test_same_problem_twice_stops_early():
    script = START + [review_with("缺结论"), DRAFT, review_with("缺结论")]
    state, _ = ma.run_pipeline("写简报", FakeLLM(script))
    assert state.status == "needs_human" and "同样的问题" in state.reason and state.revisions == 1


def test_verdict_pass_with_blocker_is_not_trusted():
    script = START + [review_with("有错", verdict="pass"), DRAFT, PASS]
    state, _ = ma.run_pipeline("写简报", FakeLLM(script))
    assert state.revisions == 1


def test_off_brief_draft_is_not_accepted():
    off = json.dumps({"verdict": "pass", "on_brief": False, "issues": []})
    state, _ = ma.run_pipeline("写简报", FakeLLM(START + [off, DRAFT, PASS]))
    assert state.revisions == 1 and state.status == "done"


def test_minor_issues_do_not_block():
    state, _ = ma.run_pipeline("写简报", FakeLLM(START + [review_with("措辞", severity="minor")]))
    assert state.status == "done" and state.revisions == 0


def test_bad_json_is_sent_back_for_retry():
    state, _ = ma.run_pipeline("写简报", FakeLLM(["不是 JSON", '{"questions": []}'] + START + [PASS]))
    assert state.status == "done"


def test_agent_that_never_returns_valid_output_fails_cleanly():
    state, _ = ma.run_pipeline("写简报", FakeLLM(["错"] * 4))
    assert state.status == "failed" and "planner" in state.reason


def test_fake_sources_are_dropped():
    fake = json.dumps({"facts": [{"id": "F1", "claim": "样本B=1.37", "source": "lab_notes.md:2"},
                                 {"id": "F2", "claim": "样本B=99", "source": "我猜的:1"}]}, ensure_ascii=False)
    state, _ = ma.run_pipeline("写简报", FakeLLM([PLAN, SEARCH, fake, DRAFT, PASS]))
    assert [f.id for f in state.notes.facts] == ["F1"] and "99" in state.notes.gaps[0]


def test_bad_reference_skips_reviewer_call():
    bad = json.dumps({"title": "t", "body": "样本B是1.37 [F9]。"}, ensure_ascii=False)
    state, ctx = ma.run_pipeline("写简报", FakeLLM([PLAN, SEARCH, NOTES, bad, DRAFT, PASS]))
    assert [c["agent"] for c in ctx.calls].count("reviewer") == 1     # 只审了修改后的版本
    assert state.status == "done"


def test_context_is_isolated():
    client = FakeLLM(START + [PASS])
    ma.run_pipeline("写简报", client)
    reviewer_request = client.requests[-1]["messages"]
    assert {m["role"] for m in reviewer_request} == {"system", "user"}  # 看不到研究员的工具调用过程
    payload = json.loads(reviewer_request[1]["content"])
    assert set(payload) == {"brief", "acceptance", "notes", "draft"}
    writer_payload = json.loads(client.requests[3]["messages"][1]["content"])
    assert "draft" not in writer_payload and writer_payload["brief"] == "写简报"


def test_tools_only_for_researcher():
    client = FakeLLM(START + [PASS])
    ma.run_pipeline("写简报", client)
    assert ["tools" in r for r in client.requests] == [False, True, True, False, False]


def test_budget_stops_the_pipeline():
    state, ctx = ma.run_pipeline("写简报", FakeLLM(START + [PASS]), max_tokens=200)
    assert state.status == "failed" and "上限" in state.reason and len(ctx.calls) == 2


def test_single_agent_for_comparison():
    text, ctx = ma.run_single("写简报", FakeLLM([SEARCH, "样本B=1.37 [lab_notes.md:2]"]))
    assert "1.37" in text and len(ctx.calls) == 2


def test_langgraph_version_takes_the_same_path():
    from graph_version import build_graph
    ctx = ma.make_context(FakeLLM(START + [review_with("缺结论"), DRAFT, PASS]))
    state = ma.State("写简报")
    build_graph(ctx).invoke({"state": state, "next": ""})
    assert state.trail == ["plan", "research", "write", "review", "revise", "review"]
    assert state.status == "done"
```

</details>

运行后应看到 `16 passed`：

```bash
uv run pytest test_multi_agent.py -q
```

## 换成 LangGraph

把同一组节点函数放进 [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)，只需 30 行。新建 `graph_version.py`：

```python
"""同一组节点函数，换成 LangGraph 编排。运行前：uv add langgraph"""
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from multi_agent import NODES, Context, State


class GraphState(TypedDict):
    state: State
    next: str


def build_graph(ctx: Context):
    graph = StateGraph(GraphState)

    def wrap(name):
        def node(gs: GraphState) -> dict:
            gs["state"].trail.append(name)
            return {"next": NODES[name](gs["state"], ctx)}
        return node

    for name in NODES:
        graph.add_node(name, wrap(name))
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "research")
    graph.add_edge("research", "write")
    graph.add_edge("write", "review")
    graph.add_conditional_edges("review", lambda gs: gs["next"], {"revise": "revise", "end": END})
    graph.add_edge("revise", "review")
    return graph.compile()
```

在这个简单例子里，它和手写的 `while` 循环效果一样（测试验证了两者路径相同）。那为什么还要用框架？因为流程变长之后，下面这些能力自己写就很费事了：

| 你需要 | LangGraph 提供 |
| --- | --- |
| 任务运行几小时，中途崩溃能恢复 | checkpointer 和[持久执行](https://docs.langchain.com/oss/python/langgraph/durable-execution) |
| 暂停，等人审批后继续 | [interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts) |
| 多个节点并行 | 图的并行分支 |
| 看每一步的状态变化 | LangSmith 集成 |

建议的顺序是：**先用手写循环搞清楚需要什么，再判断框架值不值得。** [Google ADK](https://google.github.io/adk-docs/) 也是类似的思路，用顺序、并行、循环等工作流 agent 组合多个 agent。

## 用 Claude Code Subagents 实现同样的分工

你每天用的 Claude Code 自带这套机制，不用写循环。[Subagent](https://code.claude.com/docs/en/sub-agents) 就是一个 Markdown 文件：frontmatter 定义名字、用途和权限，正文就是 system prompt。每个 subagent 有独立的上下文，只把结果返回给主对话。

新建 `.claude/agents/researcher.md`：

```markdown
---
name: researcher
description: 查找资料并整理成带来源的事实清单。需要调研时使用。
tools: Read, Grep, Glob
model: haiku
maxTurns: 10
---

你是研究员。只记录查到的事实，每条都写明“文件:行号”。查不到的列在最后。不写文章，不改文件。
```

新建 `.claude/agents/reviewer.md`：

```markdown
---
name: reviewer
description: 对照验收标准审查文稿。文稿写完后使用。
tools: Read, Grep, Glob
---

你是审稿人。只对照给你的验收标准和事实清单检查文稿，不提额外要求。
逐条输出 blocker 或 minor，最后给出“通过”或“需要修改”。不要修改任何文件。
```

然后在 Claude Code 里让它们接力：

```text
先用 researcher 查 workspace 里关于样本B的记录，然后你根据结果写一份简报到 report.md，
再用 reviewer 审查。有 blocker 就修改后再审，最多两轮。还不通过就停下来告诉我。
```

对照一下，你手写的东西在这里分别是什么：

| 手写流水线 | Claude Code |
| --- | --- |
| 独立的 `messages` | 每个 subagent 独立的上下文窗口 |
| `AgentSpec.tools` | `tools` 字段 |
| `AgentSpec.max_steps` | `maxTurns` 字段 |
| `MAX_REVISIONS` | 写在提示词里（这是软约束，模型可能不遵守） |
| 代码里的路由 | 主 agent 判断 |

最后两行是关键区别：**写在 prompt 里的是建议，写在代码里的才是规则。** 要在 Claude Code 里加硬规则，就用 [Hooks](https://code.claude.com/docs/en/hooks)。比如在 `.claude/settings.json` 里注册一个 `PreToolUse` hook，脚本以退出码 2 结束时，这次工具调用就会被拦下：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "\"$CLAUDE_PROJECT_DIR\"/.claude/hooks/no-rm.sh" }
        ]
      }
    ]
  }
}
```

```bash
#!/bin/bash
# .claude/hooks/no-rm.sh：记得 chmod +x
command=$(jq -r '.tool_input.command')
if [[ "$command" == rm* ]]; then
  echo "禁止使用 rm" >&2
  exit 2
fi
exit 0
```

和 Stage 3 讲过的一样，这个检查只是示例，换个写法（比如 `cd /tmp && rm x`）就能绕过，所以它不能当作真正的安全边界。[Subagents](https://code.claude.com/docs/en/sub-agents) 和 Hooks 的文档还覆盖了很多细节，比如 `SubagentStop` 事件、worktree 隔离和后台运行，用到时再查。

## 跨系统协作：A2A 和 ACP

上面的 agent 都在同一个程序里，直接调函数就行。跨系统时才需要协议。三个名字很像的协议，解决的是三件不同的事：

| 协议 | 连接什么 | 例子 | 通信方式 |
| --- | --- | --- | --- |
| [MCP](https://modelcontextprotocol.io/) | agent ↔ 工具和数据 | 让 agent 能查数据库 | stdio、Streamable HTTP |
| [A2A](https://a2a-protocol.org/latest/specification/)（当前版本 1.0.0） | agent ↔ agent | A 公司的 agent 委托 B 公司的 agent | HTTP、JSON-RPC 2.0、gRPC，通过 Agent Card 发现对方 |
| [ACP](https://agentclientprotocol.com/) | 编辑器 ↔ agent | 在任意编辑器里使用任意 coding agent，类似 LSP | 本地子进程用 JSON-RPC over stdio；远程支持还在完善 |

**不要提前用 A2A。** 同一个团队、同一个进程里的 agent，函数调用就够了。只有对方是别的团队或公司、你没法直接 import 它的代码时，A2A 才有意义。Stage 5 会进一步比较这些协议和 skill 的区别。

## 自检清单、常见坑与下一步

下面五项都能做到，Stage 4 就算完成了：

- [ ] 能说出每个角色的职责，以及为什么 Router 用代码而不是 agent。
- [ ] 能解释为什么路由时不直接相信 `verdict` 字段。
- [ ] 能对任何一个 agent 回答第 3 步表格里的问题。
- [ ] 能说出四种故障分别怎么防，并亲手触发过 `needs_human` 和预算超限。
- [ ] 完成了单 agent 和流水线的对比表，能根据数据说出你的场景该用哪种。

### 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| Agent 总是输出不合法 JSON | 模型在 JSON 前后加了解释 | `_extract_json` 处理；把校验错误发回让它改 |
| 没有工具的 agent 报 JSON 相关的 400 | DeepSeek 的 JSON 模式要求提示词里出现“json” | system 里已有“JSON Schema”字样，改提示词时不要删掉 |
| 审稿永远不通过 | 审稿人标准太高，或者一直提新要求 | 限定只对照 `acceptance`；把 `blocker` 的定义写清楚 |
| “同样的问题”检测不起作用 | 模型每次措辞都不同，字符串比不上 | `MAX_REVISIONS` 兜底；或给问题分类再比较 |
| 成稿悄悄加了 notes 之外的内容 | 模型用了自己的知识 | 提示词加以限制，并在 Stage 7 用评测抽查 |
| 流水线比单 agent 还差 | 交接时丢失了信息 | 产物 schema 加字段，或者改回单 agent |
| Subagent 没有被调用 | `description` 写得不清楚 | 写清楚什么时候用，或在提示词里点名 |

### 进阶练习

1. 加一个 critic 节点：专门找反例。比较有没有它的效果和成本差异。
2. 让两个研究员并行查不同的问题，用 `concurrent.futures` 实现，看总耗时变化。
3. 给流水线加人工审批：`needs_human` 时暂停，你输入意见后继续。试着用 LangGraph 的 `interrupt` 实现。
4. 把 `researcher` 的工具换成 Stage 2 的 `Toolbox`，让它能上网。
5. 给 reviser 换一个便宜的模型，reviewer 用更好的模型，看成本和质量的变化。

下一步：Stage 5 讲 skill。你会发现这一关写的各种 system prompt，其实可以打包成可以复用和分发的能力。

### 资料来源

- [Anthropic: How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Cognition: Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents)
- [Claude Code Subagents](https://code.claude.com/docs/en/sub-agents)
- [Claude Code Hooks](https://code.claude.com/docs/en/hooks)
- [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)
- [Google Agent Development Kit](https://google.github.io/adk-docs/)
- [Agent2Agent Protocol](https://a2a-protocol.org/latest/specification/)
- [Agent Client Protocol](https://agentclientprotocol.com/)
