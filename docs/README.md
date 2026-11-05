# 教程目录：从空目录逐步写出 Agent

教程主线是你自己的 `my-agent` 项目。所有新增函数、类和运行入口都在正文中给出完整定义；导入的本地模块必须是此前已经写过的文件。先让真实模型回答，再一步步增加工具和控制流程。

## 怎样跟着写

从[第 00 章](00-setup.md)创建空目录开始。每章按顺序执行：

1. 保留前一章已经写好的文件。
2. 按正文创建或替换本章文件。
3. 在自己的项目目录运行 `uv run python demo.py`，观察结果。
4. 完成最小版本后，再写增强步骤并重新运行。

例如第一章的 `from model import open_model` 指向第 00 章亲手创建的 `model.py`。正文会先解释来源，不要求读者导入仓库里的 `lessons.ch00`，也不要求复制未讲过的成品模块。

代码块标出的 `file` 是要写入的文件名，`step` 是本章检查点。它们用于标明完整文件，不属于 Python 代码。未标文件名的短片段只用来解释数据，正文会说明它放在哪里。

## 学习路线

| 章节 | 先写出的骨架 | 再增加的内容 |
| --- | --- | --- |
| [00 真实对话](00-setup.md) | 完整 HTTP 请求 | 自己提取客户端，带历史追问 |
| [01 Agent 循环](01-agent-loop.md) | 单工具请求、执行、结果回传 | 参数错误与回复一致性 |
| [02 工具注册](02-tools.md) | 名称到本地函数的映射 | 参数校验与错误反馈 |
| [03 可复用 Agent](03-chat-completions.md) | 把历史放进自己写的类 | 替换执行函数，连续对话 |
| [04 Responses，选修](04-responses.md) | 直接发送两次协议请求 | 写自己的转换客户端 |
| [05 Anthropic，选修](05-anthropic.md) | 内容块的工具往返 | 接入已有 Agent |
| [06 编码工具](06-coding-tools.md) | 读、改、检查固定练习文件 | 路径与编辑条件 |
| [07 权限](07-permissions.md) | 执行前批准或拒绝 | 只读和允许规则 |
| [08 流式](08-streaming.md) | 接收并显示文本片段 | 拼好完整工具调用再执行 |
| [09 上下文](09-context.md) | 读取约定，放进 system | 缺失文件与范围检查 |
| [10 会话与摘要](10-sessions-compaction.md) | 自己保存、加载历史 | 单独写摘要与失败处理 |
| [11 Hooks 与规划](11-hooks-planning.md) | 包装执行函数；更新计划 | 回调、计划状态约束 |
| [12 子 Agent](12-subagents.md) | 工具内部运行独立 Agent | 委派范围和共享额度 |
| [13 Skills](13-skills.md) | 列简介，再按需读取正文 | 读取范围和格式处理 |
| [14 MCP](14-mcp.md) | 自己写 echo 服务和客户端桥接 | 协议错误与进程清理 |
| [15 评测](15-evals.md) | 独立检查真实文件结果 | 汇总成功和失败案例 |
| [16 CLI](16-shipping.md) | 自己解析任务、组装 Agent | JSON 输出和安装入口 |
| [17 沙箱](17-sandbox.md) | 观察工作区内外写入区别 | 增加明确的保护规则 |
| [18 运行控制](18-runtime.md) | 记录一次请求 | 连接重试与共同期限 |

主线可从 **03 直接进入 06**。04/05 只新增自己的协议模块，后续主线不依赖它们。先完成一种协议的实际任务，再按需扩展。

## 验证正文确实能从零运行

学习时只需自己的代码与模型账号。下面这些命令供维护教程或核对手写成果，在原教程仓库根目录执行：

```bash
uv sync --locked
uv run python scripts/check_tutorial.py --reader-only
```

检查器把每个检查点的完整文件从正文写入空目录，在隔离进程里运行 `demo.py`。它禁止导入本仓库的 `lessons`、`minicode`、`examples`，并检查实际 HTTP 历史、工具结果和文件变化。离线响应由检查器注入，不写进学生项目，也不改变默认真实 API 入口。

只核对第一章，或导出第一章的最小版本：

```bash
uv run python scripts/check_tutorial.py --reader-only --chapter 1
uv run python scripts/reader_project.py --chapter 1 --step minimal --export /tmp/my-agent-ch01
```

导出目录必须为空或尚不存在。可以把自己的文件与导出文件比较，找出漏写的定义；不需要先导出才能学习。

## 完成手写后再看参考版本

仓库 `lessons/` 与 `minicode/` 保留较完整的参考版本，`examples/` 是这些版本的运行入口。它们有更多状态、异常和兼容处理，适合在理解骨架后比较。

参考阶段仍由 [`lessons/stages.json`](../lessons/stages.json) 记录，并单独验收：

```bash
uv run python scripts/check_tutorial.py --reference-only
uv run python scripts/check_tutorial.py --chapter 3 --export /tmp/minicode-reference-ch03
```

默认的 `scripts/check_tutorial.py` 会同时检查正文项目、参考阶段和本地链接。两条代码线分别验证，避免参考实现能运行，却掩盖正文缺少定义的问题。

[开源参考资料](references.md)用于完成自己的小实现后作比较。[运行参数手册](18-runtime-reference.md)描述最终参考 CLI，不作为手写课程的前置接口。
