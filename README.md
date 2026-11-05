# Agent Life From Zero

从空目录开始，用 Python 写一个能调用工具的 Agent。正文给出每一步要创建或替换的完整文件：先请求真实模型，再写工具循环，随后加入文件操作、权限、会话、规划、委派和 MCP。

## 从零开始写

打开[第 00 章](docs/00-setup.md)，在你自己的目录里创建 `my-agent`。先写 `pyproject.toml` 和 `demo.py`，发出第一次真实请求。随后亲手写出 `model.py`，再在下一章导入这个自己创建的文件。

```text
my-agent/
  pyproject.toml
  model.py       ← 第 00 章写 HTTP 客户端
  agent.py       ← 第 01 章写循环，第 03 章整理为类
  tools.py       ← 第 02 章写工具注册与执行
  demo.py        ← 每一步运行自己刚写好的代码
```

学习主线不需要安装本仓库的 `minicode`，也不导入 `lessons` 或 `examples`。每章说明保留哪些文件、创建哪些文件、在哪里替换定义。完成文件后，在自己的项目里运行：

```bash
uv sync
uv run python demo.py
```

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和支持所选协议与函数工具的模型账号。第 00 章说明环境变量配置；密钥放在终端环境中，真实请求会产生服务商费用。

## 学习路线

**真实对话 → 单工具循环 → 多工具注册 → 可复用 Agent → 编码任务 → 逐项扩展。**

第 04、05 章是 Responses 与 Anthropic 协议选修，可以从第 03 章直接进入第 06 章。每章先实现当前需要的骨架，运行后再用一个具体问题引出增强。完整路线见[教程目录](docs/README.md)。

## 验证正文能独立运行

教程的完整文件代码块就是手写项目的来源。维护检查会从空目录提取这些代码，逐个运行检查点，并禁止导入本仓库的成品模块。离线响应由检查器在外部注入，读者不必先编写假模型。

在本仓库根目录安装维护依赖后，可以检查全部正文和参考实现：

```bash
uv sync --locked
uv run python -m pytest -q
uv run python scripts/check_tutorial.py
```

也可以只检查正文，或导出一份对照文件：

```bash
uv run python scripts/check_tutorial.py --reader-only
uv run python scripts/reader_project.py --chapter 1 --step minimal --export /tmp/my-agent-ch01
```

导出是核对手写内容的辅助工具，不是学习前置步骤。导出目标必须为空或尚不存在。导出后的代码来自正文，默认调用真实 API。

## 仓库里的参考实现

```text
docs/          学生逐章手写的正文与完整文件
scripts/       从正文重建项目，检查依赖、行为与本地链接
tests/         正文项目的外部输入夹具与参考实现回归
lessons/       上一版教学小原型与完整参考阶段
examples/      参考实现的可运行实验
minicode/      功能更完整的最终参考程序
```

完成自己的版本后，可以对照参考实现如何增加协议兼容性、取消、预算和计量。原有实验仍保留，使用 `--offline` 明确运行离线回归；它们不再作为正文中跳过实现的捷径。

```bash
uv run python examples/01_agent_loop.py --offline --step minimal
uv run python scripts/check_tutorial.py --reference-only
uv run python scripts/check_tutorial.py --chapter 3 --export /tmp/minicode-reference-ch03
```

上面最后一条导出的是参考阶段；正文项目用 `reader_project.py` 导出，两者分别检查。

## 完整 CLI 与构建

以下命令在本仓库根目录运行。模型连接使用 `MINICODE_API`、`MINICODE_MODEL`、`MINICODE_BASE_URL`、`MINICODE_API_KEY`，协议可选 `openai-chat`、`openai-responses`、`anthropic-messages`：

```bash
uv run minicode --read-only -p "阅读 README，说明项目结构"
uv run minicode --stream
uv run python -m minicode.evals
uv build
```

完整 CLI 的参数和计量资料见[运行参考](docs/18-runtime-reference.md)。学习主线中的 CLI 则是你在第 16 章亲手写的 `cli.py`，两套程序的功能范围分别说明。开源实现对照见[参考资料](docs/references.md)。
