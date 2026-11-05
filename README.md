# Agent Life From Zero

从一次真实模型请求开始，用 Python 写出能调用工具的 Agent，再逐步让它读文件、修改代码、运行检查、保存会话和委派任务。

教程面向有 Python 基础的初学者。每章先写一个能运行的小版本，沿着消息历史解释它，再用具体问题引出边界处理。先学会 Agent 的骨架：**请求模型 → 执行工具 → 回传结果 → 再请求模型**。

## 先与真实模型对话

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和一个支持 Chat Completions 与函数工具的模型账号。在仓库根目录运行：

```bash
uv sync --locked
export MINICODE_MODEL="填写账号可用的模型 ID"
export MINICODE_BASE_URL="https://api.openai.com/v1"
export MINICODE_API_KEY="在当前终端填写密钥"
uv run python examples/00_hello.py
uv run python examples/01_agent_loop.py
uv run python examples/02_tools.py
```

接口地址与模型必须来自同一个服务。使用兼容服务时，填写该服务提供的 API 根地址；不要包含 `/chat/completions`。已有完整 `LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY` 的终端也可以直接运行示例。程序不自动读取 `.env`，不要把密钥写进源码或提交到仓库。真实请求会产生服务商费用。

第 00 章先收到真实回答，第 01 章让模型调用一个计算工具，第 02 章再加第二个工具。学习入口是[教程目录](docs/README.md)，每章都有最小原型和后续增强的运行命令。模型输出不必与书上的示意文字相同，重点观察请求、工具执行和历史如何连接。

## 学习路线

主线先沿一种协议做出有用的 Agent：

```text
00 真实对话 → 01 单工具循环 → 02 多工具注册 → 03 提取模型适配器
  → 06 编码任务 → 07 权限 → 08 流式 → 09 上下文 → 10 会话与压缩
  → 11 Hooks 与规划 → 12 子 Agent → 13 Skills → 14 MCP
  → 15 评测 → 16 CLI 交付 → 17 沙箱 → 18 运行控制的独立实验
```

第 04、05 章是 Responses 与 Anthropic Messages 的协议扩展。掌握第一个适配器后，可以先继续第 06 章，需要替换协议时再读它们。已提供的其他适配器不要求在主线上逐个手写。

```text
docs/          从最小实现到增强步骤的逐章教程
examples/      默认真实模型的学习入口；--offline 显式运行离线验收
lessons/       早期骨架、各章小原型与章末阶段快照
minicode/      完成全部章节后的参考实现
tests/         教学检查点、协议、工具和完整应用的回归测试
scripts/       阶段导出、检查点运行与文档链接检查
```

## 在独立目录里练习

导出只含当前及此前章节代码的工作目录：

```bash
uv run python scripts/check_tutorial.py --chapter 1 --export /tmp/minicode-ch01
cd /tmp/minicode-ch01
uv sync
uv run python examples/01_agent_loop.py --step minimal
uv run python examples/01_agent_loop.py --step enhanced
```

目标目录必须为空或尚不存在。每个阶段的 README 列出本章学习命令、离线检查点和新增文件。仓库根目录的 `minicode/` 是最终参考实现，手写时对照本章快照。

假模型和假 HTTP 响应用于稳定重现控制流，放在离线验收中。它们不再是学习真实 API 前的必修内容：

```bash
uv run python examples/01_agent_loop.py --offline --step minimal
uv run python examples/01_agent_loop.py --offline --step enhanced
```

## 完整程序

回到原教程仓库根目录，或进入第 16 章及之后的导出目录，再运行组装好的 CLI。示例协议配置如下，模型需支持选择的协议：

| `MINICODE_API` | API 根地址示例 | 请求路径 |
| --- | --- | --- |
| `openai-chat` | `https://api.openai.com/v1` | `/chat/completions` |
| `openai-responses` | `https://api.openai.com/v1` | `/responses` |
| `anthropic-messages` | `https://api.anthropic.com/v1` | `/messages` |

```bash
export MINICODE_API=openai-chat
export MINICODE_PROVIDER=openai
uv run minicode --read-only -p "阅读 README，说明这个项目的结构"
uv run minicode --stream
uv run minicode --session .minicode/session.jsonl
uv run minicode --session .minicode/session.jsonl --resume
```

CLI 默认读取 `MINICODE_*`；已有其他配置可通过 `--model`、`--base-url`、`--api-key-env` 明确指定。交互模式询问写文件和执行命令的权限；`-p` 模式默认拒绝需要确认的工具，可用 `--allow-tool` 明确允许。Bash 的系统隔离在 Linux 上通过 `--sandbox` 开启，见[第 17 章](docs/17-sandbox.md)。

其他扩展按需要开启，参数和组装过程见[第 16 章](docs/16-shipping.md)。模型参数、重试、期限、压缩、进程与计量各有独立实验，见[第 18 章](docs/18-runtime.md)。

## 检查

以下全仓检查在原教程仓库根目录运行；章节练习目录按自己的 README 验收。

```bash
uv run python -m pytest -q
uv run python scripts/check_tutorial.py
uv run python -m minicode.evals
uv build
```

章节检查逐章导出，运行每个教学检查点及累计测试，并禁止借用最终仓库中尚未引入的模块。自动检查显式使用离线输入，不消耗模型额度；真实 API 的任务效果通过各章默认学习命令观察。

三个适配器支持文本、本地函数工具、完整历史和流式输出。开源实现参考与取舍见[参考实现](docs/references.md)。
