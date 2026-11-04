# 第 9 章：Skills，把“怎么做一类事”打包成可复用的能力

## 导读

你让 agent 写过几次实验周报之后，会发现每次都要重复交代同样的事：数据从哪里取、报告用什么格式、异常值怎么标、哪些文件不能动。把这些写进 MINICODE.md？那每个会话都要为它付上下文的钱，哪怕这次根本不写周报。

Claude Code 的答案是 **skill**：一个目录，里面是一份 `SKILL.md` 操作说明，以及它需要的模板、参考资料和脚本。agent 平时只知道“有这么个 skill、什么时候用”，真要用的时候才加载完整说明。

学完你会有：

- `minicode/skills.py`：发现 skill、渐进式披露、参数替换。
- `minicode/tools/skill.py`：Skill 工具，模型通过它加载 skill。
- 一个实用的 `lab-report` skill：操作说明 + 模板 + 一个“执行但不读进上下文”的脚本。
- 能回答：skill 和 tool、prompt、MCP 分别有什么区别？装了上百个 skill，上下文怎么不爆？

## Skill、Tool、Prompt、MCP 的区别

| | 是什么 | 解决什么 | 例子 |
| --- | --- | --- | --- |
| Tool | 一个可调用的接口，有参数 schema | agent **能做**什么 | Read、Bash、WebFetch |
| Prompt / MINICODE.md | 一段文字，每次都在上下文里 | agent **始终要遵守**什么 | “注释用中文” |
| Skill | 一个目录：说明 + 资源 + 脚本，按需加载 | agent **怎样做好一类任务** | “写实验周报的标准流程” |
| MCP | 一个协议，把外部系统的能力接进来 | agent **能连接**什么 | GitHub、数据库、浏览器（第 10 章） |

一句话：tool 和 MCP 给的是“手”，skill 给的是“手艺”。

## Claude Code 是怎么做的

### 渐进式披露

Anthropic 的 [Agent Skills 文章](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)把 skill 的加载分成三层：

1. **元数据**：“At startup, the agent pre-loads the `name` and `description` of every installed skill into its system prompt.”
2. **SKILL.md 正文**：模型判断这个 skill 和当前任务相关时，才把完整的 `SKILL.md` 读进上下文。
3. **其他文件**：正文里提到的参考资料、模板等，模型需要时才去读。

脚本是一种特殊的“其他文件”：文章举的例子是一个提取 PDF 表单字段的脚本，“Claude can run this script without loading either the script or the PDF into context”。确定性的工作交给代码，既省上下文又不会算错。

### SKILL.md 的格式

Claude Code 的 [skills 文档](https://code.claude.com/docs/en/skills)：

- 位置：`~/.claude/skills/<名字>/SKILL.md`（个人）、`.claude/skills/<名字>/SKILL.md`（项目）。
- frontmatter：`name`（默认用目录名）、`description`（Claude 靠它决定什么时候用）。只有 `description` 是推荐必写的。
- 控制谁能触发：`disable-model-invocation: true` 表示只能由用户用 `/名字` 触发，模型不会自己加载；`user-invocable: false` 则相反。
- 参数：正文里的 `$ARGUMENTS` 会被替换成用户输入的参数；正文里没有占位符时，参数会以 `ARGUMENTS: <值>` 的形式附在末尾。`${CLAUDE_SKILL_DIR}` 会被替换成 skill 所在的目录，方便引用脚本。
- 文档建议 `SKILL.md` 不超过 500 行，细节放到单独的文件里。

**斜杠命令已经合并进 skill。** 文档写道：“Custom commands have been merged into skills.” `.claude/commands/deploy.md` 和 `.claude/skills/deploy/SKILL.md` 都会生成 `/deploy` 命令，行为一样。

### 不撑爆上下文

skill 的描述也占上下文。文档提到，所有 skill 的描述列表有一个总预算（大约是上下文窗口的 1%），单个描述超过 1536 个字符会被截断，超出预算时，不常用的 skill 的描述先被去掉。

### 安全

skill 是“说明 + 代码”，装一个 skill 就等于让 agent 按别人写的说明执行别人写的脚本。文章的建议是：只从可信的来源安装；安装不太可信的 skill 前完整审查一遍，特别留意让 agent 连接外部网络的指令。

## 第 1 步：发现和渲染 skill

新建 `minicode/skills.py`：

```python
"""Skills：把一类任务的做法打包成一个目录，按需加载。格式和 Claude Code 的 SKILL.md 一样。

    .minicode/skills/<名字>/SKILL.md      frontmatter（name、description）+ 正文（操作说明）
    .minicode/skills/<名字>/其他文件       参考资料、模板、脚本，正文里提到时模型再去读或执行

渐进式披露的三层：
  1. 启动时只把每个 skill 的名字和描述放进上下文（写在 Skill 工具的说明里）
  2. 模型判断用得上时调用 Skill 工具，这时才加载 SKILL.md 正文
  3. 正文里提到的其他文件，模型需要时再用 Read 或 Bash 去用
"""
import re
from dataclasses import dataclass
from pathlib import Path

from .settings import CONFIG_DIR
from .subagents import parse_frontmatter

MAX_DESCRIPTION = 1536       # 单个描述最多放多少字符进上下文（Claude Code 的上限也是这个数）
LISTING_BUDGET = 8000        # 所有描述加起来最多多少字符


@dataclass
class Skill:
    name: str
    description: str
    directory: Path
    model_invocable: bool = True      # disable-model-invocation: true 时只能由用户用 /名字 触发

    def render(self, arguments: str = "") -> str:
        """第 2 层：读出正文，替换参数占位符。"""
        _, body = parse_frontmatter((self.directory / "SKILL.md").read_text(encoding="utf-8"))
        body = body.replace("${SKILL_DIR}", str(self.directory))
        if "$ARGUMENTS" in body:
            body = body.replace("$ARGUMENTS", arguments)
        elif arguments:
            body += f"\n\nARGUMENTS: {arguments}"
        return f"<skill name=\"{self.name}\" dir=\"{self.directory}\">\n{body.strip()}\n</skill>"


def load_skills(cwd: Path, home: Path | None = None) -> dict[str, Skill]:
    skills = {}
    for root in [(home or Path.home()) / CONFIG_DIR / "skills", cwd / CONFIG_DIR / "skills"]:
        for f in sorted(root.glob("*/SKILL.md")) if root.is_dir() else []:
            meta, _ = parse_frontmatter(f.read_text(encoding="utf-8"))
            name = meta.get("name") or f.parent.name
            if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
                continue                               # 名字只允许小写字母、数字和连字符
            skills[name] = Skill(name, meta.get("description", "")[:MAX_DESCRIPTION], f.parent,
                                 meta.get("disable-model-invocation", "").lower() != "true")
    return skills


def listing(skills: dict[str, Skill]) -> str:
    """第 1 层：所有可由模型调用的 skill 的名字和描述，超出预算就截掉后面的。"""
    lines, used = [], 0
    for s in skills.values():
        if not s.model_invocable:
            continue
        line = f"- {s.name}：{s.description}"
        if used + len(line) > LISTING_BUDGET:
            lines.append("- ……（还有更多 skill 没有列出）")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)
```

`Skill` 对象只存了名字、描述和目录，**正文在 `render()` 时才读**，这就是第 2 层的“按需加载”。`listing()` 生成第 1 层的描述列表，超过预算就截断。

## 第 2 步：Skill 工具

新建 `minicode/tools/skill.py`：

```python
from ..skills import Skill, listing
from ..tool import Tool, ToolError


class SkillTool(Tool):
    name = "Skill"
    read_only = True

    def __init__(self, skills: dict[str, Skill]):
        self.skills = skills
        self.description = ("加载一个 skill（某类任务的标准做法）。当用户的任务和下面某个 skill 的描述相符时，"
                            "先调用它拿到完整说明，再严格按说明执行。已经加载过的 skill 不要重复加载。"
                            f"\n可用的 skill：\n{listing(skills) or '（没有）'}")
        self.parameters = {"type": "object", "properties": {
            "name": {"type": "string", "description": "skill 的名字"},
            "arguments": {"type": "string", "description": "传给 skill 的参数，可选"}},
            "required": ["name"]}

    def call(self, args, ctx):
        skill = self.skills.get(args["name"])
        if skill is None or not skill.model_invocable:
            raise ToolError(f"没有可用的 skill：{args['name']}。可用的有：{', '.join(self.skills) or '无'}")
        return skill.render(args.get("arguments", ""))
```

第 1 层的描述列表写在 Skill 工具的 `description` 里，随工具定义一起发给模型。模型看到列表，判断用得上，就调用 `Skill(name=...)`，拿到正文。

用户主动触发的方式在第 2 章写好的 `cli.py` 里已经有了：输入 `/lab-report 10月最后一周`，界面会直接把渲染好的正文作为一条用户消息交给 agent。

## 第 3 步：接进 app.py

在 `minicode/app.py` 里加两个导入：

```python
from .skills import load_skills
```

```python
from .tools.skill import SkillTool
```

在 Agent 工具那一行下面加：

```python
    skills = load_skills(cwd, home)                                           # 第 9 章
    if skills:
        tools.append(SkillTool(skills))
```

在创建 `Agent` 之后加一行，给 `/技能名` 命令用：

```python
    agent.skills = skills                                                     # 第 9 章：给 /技能名 命令用
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
from .skills import load_skills
from .subagents import load_agent_defs
from .tools import core_tools
from .tools.planning import ExitPlanMode, TodoWrite, plan_mode_reminder, todo_reminder
from .tools.skill import SkillTool
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
    skills = load_skills(cwd, home)                                           # 第 9 章
    if skills:
        tools.append(SkillTool(skills))
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
    agent.skills = skills                                                     # 第 9 章：给 /技能名 命令用

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

没有任何 skill 时不添加 Skill 工具，不浪费上下文。

## 跑起来：写一个实验周报 skill

```bash
mkdir -p playground/.minicode/skills/lab-report/scripts playground/notes
```

**SKILL.md**：操作说明。注意 description 写的是“什么时候用”，正文写的是“怎么做”和“怎样算做好”：

```bash
cat > playground/.minicode/skills/lab-report/SKILL.md <<'EOF'
---
name: lab-report
description: 把 notes/ 里的实验记录整理成周报。用户要求写周报、汇总或整理实验记录时使用。
---
# 实验周报

## 步骤
1. 运行 `python ${SKILL_DIR}/scripts/collect.py notes/`，它会提取所有测量数据并输出 JSON。
   直接用它的输出，不要自己逐个读记录文件去找数字。
2. 读 `${SKILL_DIR}/template.md`，按模板写报告。时间范围：$ARGUMENTS（没给就写“本周”）。
3. 把报告写到 `reports/` 目录，文件名用 `周报-<结束日期>.md`。

## 验收标准
- 报告里的每个数字都能在 collect.py 的输出里找到，并注明来源（文件:行号）。
- OD600 大于 1.0 的读数单独列为异常，并说明需要稀释后重测。

## 不要做
- 不要修改 notes/ 里的原始记录。
EOF
```

**template.md**：报告模板，只在第 2 步才被读：

```bash
cat > playground/.minicode/skills/lab-report/template.md <<'EOF'
# 实验周报（时间范围）

## 本周概况
一两句话总结。

## 测量数据
| 日期 | 样本 | 指标 | 读数 | 来源 |
| --- | --- | --- | --- | --- |

## 异常与待办
- 异常读数及处理建议

## 下周计划
EOF
```

**scripts/collect.py**：确定性的数据提取。模型只执行它、读它的输出，不需要读它的源码：

```bash
cat > playground/.minicode/skills/lab-report/scripts/collect.py <<'EOF'
"""从实验记录里提取测量数据，输出 JSON。用法：python collect.py notes/"""
import json
import re
import sys
from pathlib import Path

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}).*?(OD\d{3}|浓度)")
VALUE = re.compile(r"样本\s*(\w+?)\s*[=＝:：]\s*([\d.]+)")

rows = []
for f in sorted(Path(sys.argv[1]).glob("*.md")):
    for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
        head = LINE.match(line)
        if not head:
            continue
        for sample, value in VALUE.findall(line):
            rows.append({"date": head.group(1), "measure": head.group(2), "sample": sample,
                         "value": float(value), "source": f"{f.name}:{n}"})
print(json.dumps(rows, ensure_ascii=False, indent=1))
EOF
```

再放一份实验记录：

```bash
cat > playground/notes/week44.md <<'EOF'
# 实验记录
2026-10-28 PCR 扩增 GAPDH，退火温度 58℃，条带清晰。
2026-10-30 测 OD600：样本A=0.82，样本B=1.37，样本C=0.95。
2026-10-31 样本B 稀释 2 倍后复测 OD600：样本B=0.69。
EOF
```

启动 minicode，用两种方式触发：

- **模型自己判断**：输入 `帮我把这周的实验记录整理成周报`。模型应该先调用 `Skill(name="lab-report")`，然后按说明运行脚本、读模板、写报告。
- **用户主动触发**：输入 `/lab-report 10月28日至31日`。

检查生成的报告：每个数字是否都有来源？样本B 的 1.37 有没有被标成异常？`notes/` 有没有被改动？

## 离线测试

新建 `tests/test_ch09.py`：

```python
from minicode.agent import Agent
from minicode.skills import load_skills
from minicode.testing import FakeLLM, call
from minicode.tools.skill import SkillTool


def make_skill(root, name, text):
    d = root / ".minicode/skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d


def test_progressive_disclosure(tmp_path):
    d = make_skill(tmp_path, "release-notes",
                   "---\nname: release-notes\ndescription: 根据 git log 写发布说明\n---\n"
                   "目标版本：$ARGUMENTS\n按 ${SKILL_DIR}/template.md 的格式写。\n")
    make_skill(tmp_path, "deploy", "---\ndescription: 部署到生产\ndisable-model-invocation: true\n---\n小心")
    tool = SkillTool(load_skills(tmp_path))
    assert "release-notes：根据 git log 写发布说明" in tool.description      # 第 1 层：只有描述
    assert "目标版本" not in tool.description and "deploy" not in tool.description
    llm = FakeLLM([call("Skill", name="release-notes", arguments="v1.2"), "好"])
    events = list(Agent(llm, [tool], "s", cwd=tmp_path).query("写发布说明"))
    assert "目标版本：v1.2" in events[1]["content"]                          # 第 2 层：调用时才加载
    assert f"{d}/template.md" in events[1]["content"]
```

```bash
uv run python -m pytest tests -q
```

## 怎么知道一个 skill 真的有用

写了 skill 不代表 agent 就做得更好。skill 可能写得含糊、和 MINICODE.md 冲突，或者让 agent 在本来很简单的任务上多绕一圈。验证的办法是**对照实验**：同一组任务，装和不装这个 skill 各跑几遍，比较成功率、轮数和 token。第 11 章的评测框架可以直接做这件事（那一章的进阶练习 2）。

## 和 Claude Code 的差别

| 方面 | Claude Code | minicode |
| --- | --- | --- |
| skill 目录 | 个人、项目、子目录（按需）、插件、企业 | 个人和项目 |
| 描述预算 | 约为上下文窗口的 1%，不常用的先去掉 | 固定 8000 字符，超出的截掉 |
| 目录变量 | `${CLAUDE_SKILL_DIR}`，还有 `${CLAUDE_PROJECT_DIR}` 等 | `${SKILL_DIR}` |
| 参数 | `$ARGUMENTS`、`$0`、`$1`、具名参数 | 只有 `$ARGUMENTS` |
| 其他 frontmatter | `allowed-tools`、`model`、`context: fork`（在子 agent 里运行）、`paths` 等 | 只认 `name`、`description`、`disable-model-invocation` |
| 动态内容 | 正文里的 `` !`命令` `` 会先执行，把输出嵌进去 | 不支持 |
| 压缩后 | 用过的 skill 会被重新附上 | 不处理 |

## 自检清单

- [ ] 能用一句话分别说清 tool、prompt、skill、MCP 解决的问题。
- [ ] 能说出渐进式披露的三层，以及每一层在 minicode 的哪段代码里。
- [ ] 能解释为什么 collect.py 只执行、不读进上下文。
- [ ] 能给一个 skill 写出好的 description：说“什么时候用”，而不是“它是什么”。

## 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 模型从不调用 skill | description 写的是“这是一个周报 skill”，没写什么时候用 | 写成“用户要求写周报、汇总记录时使用” |
| 模型调用 skill 后还是自己逐个读文件 | 正文里没说清“不要自己读” | 在步骤里明确写出来 |
| 脚本路径找不到 | 正文里写了相对路径，agent 的工作目录不是 skill 目录 | 用 `${SKILL_DIR}` |
| skill 名字不生效 | 名字里有大写或下划线 | 只用小写字母、数字和连字符 |
| 装了很多 skill 后模型选错 | 描述互相重叠 | 让每个 description 有清楚的边界，必要时合并 |

## 进阶练习

1. 支持 `.minicode/commands/<名字>.md`：和 skill 一样生成 `/名字` 命令，但没有附属文件。
2. 支持 `allowed-tools`：skill 被调用后，它列出的工具在本轮内免确认（提示：可以临时往权限引擎里加 allow 规则，本轮结束后移除）。
3. 实现 `` !`命令` `` 动态内容：渲染正文时先执行命令，把输出嵌进去。想一想它的安全风险。
4. 让压缩器把本会话用过的 skill 正文重新附上，限制每个最多 2000 字符。

## 资料来源

- [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
- [Claude Code: Extend Claude with skills](https://code.claude.com/docs/en/skills)
- [Agent Skills（Claude API 文档）](https://docs.claude.com/en/docs/agents-and-tools/agent-skills)
