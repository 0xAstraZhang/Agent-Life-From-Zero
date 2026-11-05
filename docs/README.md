# 教程目录：从一个循环到完整 Agent

minicode 的目标是一个可以读代码、修改文件、运行检查、保存进度并接入外部工具的终端 Agent。你只需要 Python 基础；不要求提前写过聊天机器人，也不要求准备模型密钥。

## 学习路线

| 章节 | 这一章新增什么 | 可运行实验 |
| --- | --- | --- |
| [00 环境与第一次模型交互](00-setup.md) | 请求、回复、工具外部执行的概念 | `examples/00_hello.py` |
| [01 最小 Agent Loop](01-agent-loop.md) | 统一消息与假模型，串行工具往返 | `examples/01_agent_loop.py` |
| [02 工具系统](02-tools.md) | 工具声明、注册、参数校验和统一结果 | `examples/02_tools.py` |
| [03 Chat Completions](03-chat-completions.md) | 第一个真实协议适配器 | `examples/03_chat_completions.py` |
| [04 Responses](04-responses.md) | items、call_id 和原始续轮数据 | `examples/04_responses.py` |
| [05 Anthropic Messages](05-anthropic.md) | 内容块、结果分组、三协议契约测试 | `examples/05_anthropic.py` |
| [06 编码工具](06-coding-tools.md) | 读、写、改、查找、搜索、运行命令 | `examples/06_coding_tools.py` |
| [07 权限](07-permissions.md) | 执行边界、共享审批、只读模式 | `examples/07_permissions.py` |
| [08 流式输出](08-streaming.md) | SSE、事件归一、参数拼装、中断 | `examples/08_streaming.py` |
| [09 上下文](09-context.md) | 项目说明与请求视图 | `examples/09_context.py` |
| [10 会话与压缩](10-sessions-compaction.md) | 恢复、调用修补、派生摘要 | `examples/10_sessions.py` |
| [11 Hooks 与规划](11-hooks-planning.md) | 执行扩展点、计划工具与只读策略 | `examples/11_hooks_planning.py` |
| [12 子 Agent](12-subagents.md) | 独立历史、只读委派、共享预算 | `examples/12_subagents.py` |
| [13 Skills](13-skills.md) | 发现技能、按需读取操作指南 | `examples/13_skills.py` |
| [14 MCP](14-mcp.md) | 把外部协议的工具接入同一执行器 | `examples/14_mcp.py` |
| [15 评测](15-evals.md) | 文件验收、用量预算、失败分类 | `examples/15_evals.py` |
| [16 交付](16-shipping.md) | CLI、配置、打包和干净安装 | `examples/16_shipping.py` |
| [17 沙箱](17-sandbox.md) | bubblewrap 隔离 Bash、受保护路径、断网 | `examples/17_sandbox.py` |
| [18 运行控制与记录](18-runtime.md) | 思考参数、重试、任务期限、长任务压缩、进程会话和用量明细 | `examples/18_runtime.py` |

## 开始之前：环境准备

在仓库根目录执行：

```bash
uv sync --locked
uv run python examples/00_hello.py
```

`uv sync` 按锁文件安装项目本身和开发依赖。示例脚本因此可以导入 `minicode`。不用 `cd` 到 `examples`，也不用手动设置 `PYTHONPATH`。详细排错见[第 00 章](00-setup.md)。

## 怎样使用代码

第一次阅读时，先看每章的实验，再读本章新增模块。[第 01 章快照](../lessons/ch01.py)只依赖标准库，可以从空文件开始手写；[第 02 章快照](../lessons/ch02.py)在它上面提取 `Agent` 与 `ToolRunner`，没有后续章节的流式分支。第 03 章才把类型和循环拆成正式模块并增加协议所需字段，第 08 章再加入流式处理。

后续扩展通过包装器组合：权限包装工具执行器，预算和压缩包装模型，会话在循环外记录内部消息。增加 Skills、MCP 等工具时扩充内层注册表，继续保留已有权限和 hooks。CLI 在第 16 章组装，第 17 章用同样的包装方式为 Bash 加入 Linux 沙箱，第 18 章把运行参数和请求记录补齐，支持可复现的真实模型实验。

## 每章的完整工作目录

[阶段清单](../lessons/stages.json)列出每章新增或替换哪些文件。早期版本保存在 `lessons/stages/`；模块在后续不再变化时，清单复用已经引入的文件。导出器按章累计复制，不把最终仓库直接当作每章产物。

例如，在仓库根目录导出第三章：

```bash
uv run python scripts/check_tutorial.py --chapter 3 --export /tmp/minicode-ch03
```

然后在导出目录按 README 运行和修改。目录必须为空或尚不存在，防止覆盖练习内容。每章正文区分三类内容：完整文件链接、需要替换的完整定义、只说明数据形状的片段。没有标题要求时，不把解释片段随意拼到程序末尾。

只检查第三章，或检查全部阶段：

```bash
uv run python scripts/check_tutorial.py --chapter 3
uv run python scripts/check_tutorial.py
```

验收会在临时目录导出阶段文件，移除最终仓库的导入路径，并禁止缺失的 `minicode`、`lessons` 等模块回落到本机已安装的包。因此，误引入未来章节会直接失败。章节测试各自独立，`-k` 不再被用来绕开提前导入的问题。

第 03 章开始还可在已配置环境变量后运行 `examples/live_model.py`，先验证非流式工具往返；第 04、05 章增加协议，第 08 章增加 `--stream`。真实请求与默认离线验收分开执行。

每完成一章，做四件事：

1. 运行本章示例，观察新增的消息或事件。
2. 阅读并实现本章模块，解释它的输入输出。
3. 运行本章测试，再运行已有回归测试。
4. 修改一个条件，例如未知工具、拒绝权限或截断响应，预测并验证结果。

## 始终遵守的边界

```mermaid
flowchart LR
    UI[CLI / 章节实验] --> Agent[Agent 循环]
    Agent --> Model[ModelClient]
    Model --> Chat[Chat Completions]
    Model --> Responses[Responses]
    Model --> Anthropic[Anthropic Messages]
    Agent --> Runner[ToolRunner]
    Runner --> Tools[本地工具 / MCP 工具]
```

- 内部只有 `Message`、`ToolSpec`、`ToolCall`、`ToolResult`、`ModelReply` 等类型。
- 适配器负责协议转换，不执行工具，不决定权限，不写会话。
- Agent 决定继续或停止；工具执行器负责参数验证和调用。
- 会话保存内部历史；模型请求可以使用它的派生视图。
- 服务商续轮数据由适配器保存、核对来源和重放，核心不解释它。

## 阅读开源实现

[参考实现](references.md)固定了 Codex、OpenCode、pi 的版本，并把本教程的模块映射到源码位置。先做出自己的小实现，再阅读对应模块，比较状态、边界和失败处理，不需要先读完整仓库。
