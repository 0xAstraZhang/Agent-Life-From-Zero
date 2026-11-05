# Agent Life From Zero

从零用 Python 构建 minicode：内部统一消息、工具调用与工具结果，外部适配 **OpenAI Chat Completions、OpenAI Responses、Anthropic Messages**。

教程共 00–17 章。先用假模型观察最小循环，再实现三个协议适配器，然后加入编码工具、权限、流式输出、上下文、会话、规划、子 Agent、Skills 和 MCP，接着评测与打包，最后为 Bash 加上 Linux 沙箱。设计参考 Codex、OpenCode 和 pi，源码版本与取舍见[参考实现](docs/references.md)。

## 从这里开始

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。所有命令都在仓库根目录运行：

```bash
uv sync --locked
uv run python examples/00_hello.py
uv run python examples/01_agent_loop.py
uv run minicode --demo
```

以上命令用于预览成品，不需要 API Key。跟着手写时，打开[教程目录](docs/README.md)，每章只使用该阶段的完整文件与测试。例如导出第三章：

```bash
uv run python scripts/check_tutorial.py --chapter 3 --export /tmp/minicode-ch03
```

导出目录包含累计到本章的代码、测试和运行说明；不会复制后续模块。原仓库的 `minicode/` 是最终参考实现。

```text
docs/          逐章解释：为什么加、怎么加、如何验收
examples/      每章能单独运行的实验，默认使用假模型或假 HTTP 服务
lessons/       阶段清单与早期完整文件，记录每章新增和替换
minicode/      最终代码：内核、协议适配、工具与扩展
tests/         循环、协议、工具和端到端离线回归
scripts/       阶段导出、隔离验收与文档链接检查
```

## 调用真实模型

显式选择协议、模型和接口地址。`api` 表示协议，`provider` 表示服务来源。

| `MINICODE_API` | 默认 `MINICODE_BASE_URL` | 请求路径 |
| --- | --- | --- |
| `openai-chat` | `https://api.openai.com/v1` | `/chat/completions` |
| `openai-responses` | `https://api.openai.com/v1` | `/responses` |
| `anthropic-messages` | `https://api.anthropic.com/v1` | `/messages` |

```bash
export MINICODE_API="openai-responses"
export MINICODE_MODEL="填写账号可用且支持该协议的模型 ID"
export MINICODE_PROVIDER="openai"
export MINICODE_BASE_URL="https://api.openai.com/v1"
```

在同一个终端通过环境变量设置 `MINICODE_API_KEY`，不要写进仓库。程序不会自动读取 `.env`。真实请求会产生服务商费用。

从第三章就可以验证真实工具往返，不需要等到最终 CLI。若已有完整的 `LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY` 配置：

```bash
uv run python examples/live_model.py --env-prefix LLM --api openai-chat
```

这个入口只读取指定的一组配置，不混用不同来源的密钥。DeepSeek 的 Chat 请求使用 `max_tokens`；OpenAI 默认使用 `max_completion_tokens`。可通过 `--chat-token-field` 显式选择，CLI 对应环境变量为 `MINICODE_CHAT_TOKEN_FIELD`。具体说明见[第 03 章](docs/03-chat-completions.md)。

```bash
uv run minicode --read-only -p "阅读 README，说明这个项目的结构"
uv run minicode --stream
```

最终 CLI 默认读取 `MINICODE_*`。继续使用已有 DeepSeek `LLM_*` 配置时，明确传递来源和密钥变量名：

```bash
uv run minicode --api openai-chat --provider deepseek \
  --model "$LLM_MODEL" --base-url "$LLM_BASE_URL" --api-key-env LLM_API_KEY \
  --read-only -p "阅读 README，说明项目结构"
```

交互模式会询问写文件和执行命令的权限。`-p` 非交互模式默认拒绝需要确认的工具；可以用 `--allow-tool Edit` 等参数显式允许某种操作。文件工具限制在工作目录内，但文件路径检查不能隔离 shell：**默认情况下 Bash 没有操作系统沙箱**。在 Linux 上可以加 `--sandbox`，用 bubblewrap 运行 Bash，见下文。

会话和扩展在学到对应章节后再开启：

```bash
uv run minicode --session .minicode/session.jsonl
uv run minicode --session .minicode/session.jsonl --resume
uv run minicode --cwd playground --instructions MINICODE.md --skills playground/skills --subagents
```

最后一条需要先按第 09、13 章创建项目说明和技能目录。

Linux 上安装 bubblewrap 后，可以让 Bash 在沙箱中运行：只有工作区可写，`.git`、`.minicode` 等路径只读，默认断网，密钥类环境变量被移除。沙箱不可用时程序直接退出，不会退回无沙箱执行：

```bash
uv run minicode --sandbox
uv run minicode --sandbox --allow-tool Bash -p "运行测试，找出失败原因"
uv run minicode --sandbox --sandbox-writable ~/.cache/uv --sandbox-network
```

沙箱不限制读取，也不覆盖 MCP 子进程；范围见[第 17 章](docs/17-sandbox.md)。`--mcp-command` 会启动你明确指定的本地程序；教程不会自动加载或执行项目里的 hooks、技能脚本或 MCP 配置。

## 检查

```bash
uv run python -m pytest -q
uv run python scripts/check_tutorial.py
uv run python -m minicode.evals
uv build
```

章节检查在每章独立目录运行，并阻止导入仓库最终包，确保不借用未来代码。离线检查不代表真实模型的任务成功率；在线评测与费用记录见[第 15 章](docs/15-evals.md)。

## 实现范围

三个适配器支持文本、本地函数工具、多轮完整历史、用量与停止状态、SSE 流式输出；保存同来源续轮所需的原始响应数据。教学实现不提供图片和音频输入、服务商托管工具或无损跨模型推理续传；操作系统沙箱只有 Linux 上基于 bubblewrap 的基础版本（第 17 章）。MCP 实现固定版本的 stdio/tools 子集，范围见[第 14 章](docs/14-mcp.md)。
