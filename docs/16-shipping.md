# 第 16 章：组装 CLI 并交付

上一章：[评测](15-evals.md) · [目录](README.md) · 下一章：[沙箱](17-sandbox.md)

## 这一章的结果

这一章把前面独立验证的组件接成可安装的程序。本章的入口是 [cli.py](../lessons/stages/16/minicode/cli.py)，包配置在 [pyproject.toml](../pyproject.toml)。第 17 章会在这个文件里增加沙箱参数，最终版本见 [minicode/cli.py](../minicode/cli.py)。CLI 负责读配置、组装组件和显示事件，不复制 Agent Loop。

本章新增 `cli.py`、`__main__.py`、控制台入口和 CLI 测试；保留第 15 章以前的全部模块。完整累积目录可在仓库根目录导出：

```bash
uv run python scripts/check_tutorial.py --chapter 16 --export /tmp/minicode-ch16
```

进入导出目录后运行 `uv sync` 安装本阶段程序，再执行以下命令。前面章节不会安装这个尚未实现的 CLI 入口。

## 第一步：检查最终依赖方向

```text
CLI
 ├─ ModelConfig → 三协议工厂 → HTTPModel
 │                 ↑ BudgetModel ↑ CompactingModel
 ├─ ToolRunner ← 文件工具 / 计划 / Skills / MCP / 只读委派
 │     ↑ PermissionRunner（共享 Approval）
 └─ Agent → 统一 Message 和结果事件
       ↑ run_session（保存完整历史）
```

没有 `if anthropic` 出现在 Agent、权限或会话里。只有工厂按 `api` 选择适配器。`provider`、`model`、`base_url` 是配置数据；同一个服务商可以提供多种协议。

## 第二步：配置和组装

CLI 从环境变量读取默认模型配置，命令行参数可以覆盖它：

| 参数 | 用途 |
| --- | --- |
| `--api`、`--model`、`--base-url`、`--provider` | 模型协议与来源 |
| `--api-key-env` | 指定读取密钥的环境变量名，不接收明文密钥参数 |
| `--cwd` | 文件工具和命令的工作目录 |
| `--max-turns`、`--token-budget`、`--max-output-tokens` | 轮数、累计软预算、单次输出上限 |
| `--chat-token-field` | Chat 协议的输出上限字段：`max_tokens` 或 `max_completion_tokens` |
| `--read-only`、`--allow-tool` | 只读限制、显式工具授权 |
| `--stream`、`--json` | 模型流式输出、逐行 JSON 运行事件 |
| `--session`、`--resume` | 保存和恢复会话 |
| `--instructions`、`--skills`、`--subagents` | 项目说明、技能目录、只读委派 |
| `--mcp-command` | 显式启动一个 stdio MCP 服务 |

`--json` 只是事件的输出格式；`--stream` 才决定是否向服务端请求 SSE。两者可以独立使用。

`--provider deepseek` 默认选择 `max_tokens`，其他来源保留 OpenAI 的 `max_completion_tokens`；显式 `--chat-token-field` 优先。字段选择只在模型配置和适配器边界处理，不进入 Agent。环境变量 `MINICODE_CHAT_TOKEN_FIELD` 也可以设置该选项。

CLI 用 `ExitStack` 管理 HTTP 客户端和 MCP 子进程的生命周期，正常退出、配置异常和用户中断都经过同一条清理路径。

## 第三步：先验收离线入口

```bash
uv run minicode --help
uv run minicode --demo
uv run minicode --demo --stream --json
uv run python examples/16_shipping.py
```

`--demo` 运行固定剧本：Glob → 工具结果 → 最终回答。它不理解任意任务，也不请求真实模型；它用来验证配置、组装、事件输出是否接通。

JSON 模式中应依次看见 `assistant`、`tool_result`、`assistant`、`result`。离线 demo 开启 `--stream` 后还会出现文本增量；工具参数增量在第 08 章的三个协议实验中观察。最终结果仍然只有一个。

## 第四步：交互与非交互

配置真实模型后：

```bash
uv run minicode
uv run minicode --read-only -p "查看项目结构并总结"
uv run minicode --allow-tool Edit -p "阅读 answer.py，将错误的常量改正并说明依据"
```

交互模式读终端输入，用户输入 `exit` 或 `quit` 退出。`-p` 模式没有审批回调：需要授权的工具默认拒绝，只有显式允许的工具名例外。`--read-only` 的限制优先于 allow 列表。

程序退出码：成功为 0，任务未成功为 1，配置或外层运行错误为 2，外层键盘中断为 130。具体任务停止原因仍保留在 `result.status` 中。

文件工具会核对工作目录与符号链接，但 Bash 是普通子进程，不能提供 OS 沙箱。[第 17 章](17-sandbox.md)在 Linux 上用 bubblewrap 为 Bash 加一层隔离；无人值守地运行不可信项目时，仍需要另外配置容器或系统隔离。

## 第五步：保存和恢复

```bash
uv run minicode --session .minicode/work.jsonl
uv run minicode --session .minicode/work.jsonl --resume
```

已有会话必须明确使用 `--resume`，避免意外覆盖。恢复加载内部消息，缺失的工具结果补为“结果未知”，不会自动再次执行写入。CLI、会话和模型适配器仍各自只处理自己的边界。

默认保留会话中的应用说明。如果本次显式选择 `--instructions` 或 `--skills`，CLI 按当前配置重新构建 system 消息，保留用户、助手和工具历史。需要继续保留的自定义说明与技能目录应在本次参数中一并指定。

开启不同模型或协议后，适配器只重用来源匹配且内部内容未被编辑的原始续轮数据。文本与工具历史可以转换；专属推理状态不保证能跨模型延续。

## 第六步：构建安装包

```bash
uv build
```

构建得到 wheel 和源代码包。项目的命令行声明是：

```toml
[project.scripts]
minicode = "minicode.cli:main"
```

可以在新环境中安装 wheel，确认没有依赖源码目录：

```bash
uv venv /tmp/minicode-clean
uv pip install --python /tmp/minicode-clean/bin/python dist/agent_life_from_zero-0.2.0-py3-none-any.whl
/tmp/minicode-clean/bin/minicode --help
/tmp/minicode-clean/bin/minicode --demo
```

这里的临时目录命令适用于 macOS/Linux；Windows 使用对应虚拟环境的 `Scripts` 路径。实际发布前还应在目标系统验证命令执行与子进程中断。

## 发布前的验收

```bash
uv run python -m pytest -q
uv run python scripts/check_tutorial.py
uv run python -m minicode.evals
uv build
```

同时完成一次有记录的真实模型检查。离线检查不请求模型服务，不能代替在线协议兼容性、账户可用性和模型任务效果验证。

其中 `scripts/check_tutorial.py` 在原教程仓库运行，用来验收所有阶段；导出的第 16 章工作目录按其 README 运行累计测试和当前实验即可。

README 应包含：安装命令、三协议配置、离线示例、真实请求方式、权限行为、检查命令和实现范围。本仓库的 [README](../README.md) 就是对应成品。

## 完成后的扩展方向

现在的系统已经有完整的“接收任务 → 请求模型 → 校验并执行工具 → 回传结果 → 验证并停止”路径。

下一章先为 Bash 加上 Linux 沙箱。之后可以继续选择一个真实需求扩展：图片工具结果、跨平台沙箱、写任务的独立工作树、流式重连或远程 MCP。每增加一种能力，都先决定它属于哪一层，再增加行为测试。新增模型协议仍不应要求复制主循环。
