# 第 12 章：交付，让别人也能用你的 agent

## 导读

一个只能在你自己电脑上、靠你记得的一串命令跑起来的 agent，还不算一个真正的产品。“真实可用”至少要满足这几条：有明确的用户、任务和成功标准；有日志、超时、成本上限；有权限边界和人工确认；有部署方式；有 README。

前面十一章已经把能力和保障做齐了，这一章把它们包装成别人能用的东西：

- 一张产品卡片，先想清楚给谁用、做什么、怎样算成功。
- 把 minicode 打包成一个能 `uv tool install` 的命令行工具。
- 三种使用方式：交互式、非交互式（脚本和 CI）、作为 Python 库。
- 三个 GitHub Actions 工作流：离线测试、定期评测、只读的 PR 审查。
- README 模板和发布前的检查清单。

## Claude Code 是怎么交付的

Claude Code 本身就是一个交付得很好的例子，它同时提供了好几种用法：

| 用法 | Claude Code | minicode 对应 |
| --- | --- | --- |
| 交互式 | 终端里的 `claude`，以及桌面、网页、IDE 插件 | `minicode` |
| 非交互式 | `claude -p`，`--output-format json` / `stream-json` | `minicode -p` |
| 作为库 | Claude Agent SDK（Python、TypeScript）的 `query()` | `build_agent()` + `agent.query()` |
| CI | 官方的 GitHub Actions，可以在 PR 和 issue 里调用 | 下面的工作流 |
| 配置 | 用户、项目、本地三层 settings，CLAUDE.md，`.mcp.json` | `.minicode/` 下的同名文件 |

一个值得学的地方：**这些用法共用同一个内核。** 交互式界面、`-p`、SDK 都消费同一串事件，区别只在于事件流向哪里、谁来回答权限确认。minicode 从第 1 章起就是这样设计的，所以这一章几乎不需要写新的核心代码。

## 第 1 步：先写产品卡片

在写 README 之前，先回答这几个问题。答不出来，说明还没想清楚要交付什么：

```markdown
# 产品卡片：minicode

- 用户：在终端里工作、想学习 agent 原理的 Python 开发者。
- 任务：在一个本地代码目录里读代码、改 bug、跑测试、回答关于代码的问题。
- 成功标准：评测集（evals/tasks.json，至少 20 个任务）每个任务跑 3 次，全部通过的比例 ≥ 80%；
  单个任务平均花费 ≤ 0.05 美元；没有任何一次越过权限边界。
- 不做什么：不操作工作目录以外的文件；不在无人值守时执行未经允许的命令；不登录任何账号。
- 风险和对策：误删文件（deny 规则 + 先读后改）；提示词注入（数据不是指令 + 权限）；
  成本失控（max_turns + max_budget_usd）。
```

成功标准要写成能测量的数字，并且和第 11 章的评测对得上。

## 第 2 步：打包成命令行工具

把 minicode 放进一个单独的项目目录，加一个 `pyproject.toml`：

```bash
mkdir -p ~/code/minicode-agent
```

```bash
cp -r minicode ~/code/minicode-agent/
```

在 `~/code/minicode-agent/` 下新建 `pyproject.toml`：

```toml
[project]
name = "minicode-agent"
version = "0.1.0"
description = "照着 Claude Code 架构写的迷你 coding agent"
requires-python = ">=3.12"
dependencies = ["openai>=2.54.0"]

[project.scripts]
minicode = "minicode.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["minicode"]
```

`[project.scripts]` 把 `minicode` 命令指向 `cli.py` 的 `main()`。`mcp` 不在依赖里：minicode 的 MCP 客户端是自己写的，只有写服务器时才需要那个包。

构建并安装成全局命令：

```bash
cd ~/code/minicode-agent
```

```bash
uv build
```

```bash
uv tool install dist/minicode_agent-0.1.0-py3-none-any.whl
```

```bash
minicode --help
```

之后在任何目录里都可以直接运行 `minicode`，它会把当前目录当作工作目录。卸载用 `uv tool uninstall minicode-agent`。

## 第 3 步：三种用法

**交互式**，日常使用：

```bash
minicode
```

**非交互式**，给脚本和 CI 用。没有人确认，所以要用 `--allowed-tools` 明确允许需要的操作，用 `--max-turns` 和 `--max-budget-usd` 兜底：

```bash
minicode -p "运行测试，如果失败就修好" --permission-mode acceptEdits \
  --allowed-tools "Bash(python -m pytest *)" --max-turns 30 --max-budget-usd 0.2
```

`--output-format json` 输出最终结果（含 `session_id`），可以接着用 `--resume` 继续同一个会话：

```bash
minicode -p "找出所有 TODO 注释" --output-format json > result.json
```

```bash
minicode -p "把第一个 TODO 实现掉" --resume "$(python -c "import json; print(json.load(open('result.json'))['session_id'])")"
```

**作为 Python 库**，嵌进你自己的程序。用法和 Claude Agent SDK 的 `query()` 很像：传入任务，遍历事件：

```python
from minicode.app import build_agent

agent = build_agent("playground", mode="dontAsk", allowed_tools=["Read", "Grep", "Glob"], with_mcp=False)
for event in agent.query("列出所有 TODO 注释，按文件分组"):
    if event["type"] == "tool_result" and event["is_error"]:
        print("工具出错：", event["content"][:200])
    elif event["type"] == "result":
        print(event["subtype"], f"${event['cost_usd']}")
        print(event["text"])
```

要在自己的界面里处理权限确认，就传一个 `ask_user` 函数进去，签名和 `cli.py` 里的一样：`(工具名, 参数, 理由) -> "yes" | "always" | "no"`。

## 第 4 步：接入 GitHub Actions

三个工作流各管一件事，放在仓库根目录的 `.github/workflows/` 下。

**一、离线测试**：每次提交都跑。只用假模型，不花钱，不需要任何密钥。新建 `.github/workflows/tests.yml`：

```yaml
# 每次提交都跑：只用假模型，不花钱，也不需要任何密钥
name: tests

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  offline-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --locked
      - name: Offline tests
        working-directory: agent-life-from-zero
        run: uv run python -m pytest tests -q
```

**二、真模型评测**：要花钱，所以只在手动触发和每周定时运行。密钥放进仓库的 Secrets（`LLM_API_KEY`），接口地址和模型名放进 Variables（`LLM_BASE_URL`、`LLM_MODEL`）。新建 `.github/workflows/evals.yml`：

```yaml
# 用真模型跑评测要花钱：只在手动触发和每周定时运行，不在每个 PR 上自动跑
name: evals

on:
  workflow_dispatch:
  schedule:
    - cron: "17 3 * * 1"

permissions:
  contents: read

jobs:
  evals:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --locked
      - name: Run evals with a real model
        working-directory: agent-life-from-zero
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_BASE_URL: ${{ vars.LLM_BASE_URL }}
          LLM_MODEL: ${{ vars.LLM_MODEL }}
        run: uv run python evals/run_evals.py evals/tasks.json --repeat 2 --out evals/results.jsonl
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: eval-results
          path: agent-life-from-zero/evals/results.jsonl
```

**三、只读的 PR 审查**：让 minicode 审查每个 PR 的改动，结果写进这次运行的摘要页。新建 `.github/workflows/review.yml`：

```yaml
# 用 minicode 只读地审查 PR 的改动，结果写进这次运行的摘要页
name: minicode-review

on:
  pull_request:

permissions:
  contents: read

jobs:
  review:
    # 只审查本仓库分支发起的 PR。fork 发起的 PR 拿不到密钥，而且它的内容完全不可信
    if: github.event.pull_request.head.repo.full_name == github.repository
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --locked
      - name: Review the diff (read-only)
        working-directory: agent-life-from-zero
        env:
          LLM_API_KEY: ${{ secrets.LLM_API_KEY }}
          LLM_BASE_URL: ${{ vars.LLM_BASE_URL }}
          LLM_MODEL: ${{ vars.LLM_MODEL }}
          BASE_REF: ${{ github.base_ref }}
        run: |
          uv run python -m minicode -p "审查当前分支相对 origin/$BASE_REF 的改动：先用 git diff 看改了什么，再读相关代码。只报告真实存在的问题，按严重程度排序，每条写清文件路径和行号。" \
            --cwd .. --permission-mode dontAsk --max-turns 20 --max-budget-usd 0.2 \
            --allowed-tools Read Grep Glob "Bash(git diff *)" "Bash(git log *)" \
            --disallowed-tools Write Edit WebFetch Agent \
            >> "$GITHUB_STEP_SUMMARY"
```

这个工作流的每一行配置都在防点什么：

| 配置 | 防什么 |
| --- | --- |
| `on: pull_request`，而不是 `pull_request_target` | `pull_request_target` 会把密钥交给处理不可信代码的流程，是 GitHub Actions 里最常见的安全事故来源之一 |
| 只审查本仓库分支的 PR | fork 来的 PR 本来就拿不到密钥；它的内容也完全不可信 |
| `permissions: contents: read` | 即使 agent 被 PR 里的内容注入，它手里的 token 也改不了仓库 |
| `--permission-mode dontAsk` + 只读的 allowed-tools | PR 的代码和描述都可能藏着指令；agent 只能读、只能跑 `git diff` 和 `git log` |
| `--disallowed-tools Write Edit WebFetch Agent` | 直接从工具列表里拿掉，模型连尝试的机会都没有；WebFetch 被拿掉，也就没有把代码发到外网的通道 |
| `--max-turns` 和 `--max-budget-usd` | 防止一个奇怪的 PR 让它无限循环、花光预算 |
| `BASE_REF` 通过环境变量传入 | 不把 `${{ }}` 表达式直接拼进 shell 命令，避免命令注入 |
| 结果写进摘要页，而不是自动评论 | 不需要给 token 写权限；人看过再决定要不要采纳 |

## 第 5 步：写 README

README 是给“第一次见到这个项目的人”写的。下面是一个模板，每一节都对应用户的一个问题：

```markdown
# minicode

照着 Claude Code 架构写的迷你 coding agent：在终端里读代码、改 bug、跑测试。

## 快速开始
（安装命令、配置三个环境变量、跑第一个任务。五分钟内能看到结果。）

## 配置
（环境变量表；.minicode/settings.json 的示例；MINICODE.md 是什么。）

## 使用
（交互式、-p、作为库，各给一个例子；斜杠命令列表。）

## 安全边界
（默认会问什么、不会做什么；推荐的 deny 规则；在 CI 里怎么用；不要在不信任的仓库里运行。）

## 扩展
（怎么加工具、写 hook、写 skill、写子 agent、接 MCP 服务器，各指向一个例子。）

## 测试和评测
（离线测试命令；评测命令；最近一次评测的结果和日期。）

## 已知限制
（下面列的那些，以及你在评测里发现的失败类型。）
```

## 发布前的检查清单

- [ ] 在一台干净的机器（或者一个新容器）上，只看 README 能装好并跑通第一个任务。
- [ ] `uv run python -m pytest tests -q` 全部通过。
- [ ] 真模型评测跑过一次，结果记在 README 里，成功率达到产品卡片里的标准。
- [ ] `git log -p | grep -i "sk-"` 之类的检查确认没有提交过密钥；`.env`、`playground/`、会话记录都在 `.gitignore` 里。
- [ ] 默认配置是安全的：不加任何参数时，修改文件和执行命令都会询问。
- [ ] `-p` 模式下没有 allowed-tools 时，需要确认的操作都被拒绝，而不是卡住等输入。
- [ ] 每个出错信息都告诉用户下一步该做什么（缺 key、配置文件写错、MCP 服务器起不来……）。
- [ ] README 里写了已知限制和费用说明。

## minicode 的已知限制

这些是为了教学而做的简化，每一章末尾的“和 Claude Code 的差别”有更细的列表：

- 只支持 OpenAI 兼容的 Chat Completions 接口，不支持流式输出。
- 权限规则按字符串拆分命令，不理解 shell 语法；没有沙箱，只有容器方案。
- 没有文件检查点和回滚；没有会话分叉。
- MCP 只支持 stdio 传输和工具，不支持资源、提示词和 OAuth；工具很多时没有按需加载。
- 子 agent 不能嵌套，也不能在后台运行。
- 没有目录信任机制：项目里的 hooks、skills、MCP 配置会被直接使用。

## 走到这里，你学会了什么

回头看第 1 章那句话：**agent = 一个循环 + 一个消息列表 + 一张工具表。** 这句话没有错，但十二章下来你应该已经体会到，一个能用的 agent，大部分代码都花在这句话以外的地方：

| 你写的 | 它回答的问题 |
| --- | --- |
| 工具系统 | 模型能做什么，怎样做才不容易出错 |
| 权限和 hooks | 什么能做、什么不能做、什么要先问 |
| 上下文工程 | 模型每一轮应该看到什么 |
| 会话和压缩 | 中断了怎么接着做，上下文满了怎么办 |
| 规划和子 agent | 长任务怎么不迷路，怎么不被细节淹没 |
| skills 和 MCP | 能力怎么复用、怎么接入外部世界 |
| 评测和安全 | 怎么知道它做得好不好、会不会闯祸 |

这些正是 harness 的全部内容，也是 Claude Code 这类产品真正的工作量所在。

## 下一步

- **用真正的工具。** 现在你知道 Claude Code 的每个机制在做什么了，去读它的[官方文档](https://code.claude.com/docs/en/overview)，你会发现每一页都能对上号。需要在自己的产品里嵌入 agent 时，优先考虑 [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/agent-loop) 这类成熟的实现，而不是从零维护一个 minicode。
- **读别人的实现。** [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 用另一种方式从零复刻了同样的机制；[OpenAI Codex CLI](https://github.com/openai/codex)、[OpenHands](https://github.com/All-Hands-AI/OpenHands)、[Aider](https://github.com/Aider-AI/aider) 是完整的开源 coding agent。对照着读，看它们怎么处理权限、上下文和工具。
- **换一个场景。** 把 minicode 的工具换掉：给研究助手配上搜索和引用校验，给运维助手配上只读的监控查询。harness 的结构不用变。

## 资料来源

- [Claude Code overview](https://code.claude.com/docs/en/overview)
- [Claude Code: Run Claude Code programmatically](https://code.claude.com/docs/en/headless)
- [Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions)
- [GitHub: Security hardening for GitHub Actions](https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions)
- [uv: Building and publishing a package](https://docs.astral.sh/uv/guides/package/)
- [uv: Using tools](https://docs.astral.sh/uv/guides/tools/)
