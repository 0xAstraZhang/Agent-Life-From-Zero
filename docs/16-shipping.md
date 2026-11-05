# 第 16 章：亲手写出命令行入口

上一章：[评测](15-evals.md) · 下一章：[沙箱](17-sandbox.md)

继续使用自己的 `my-agent/`。先把终端的一句话传给已经写好的 Agent，再把结果打印出来。之后只增加一个显示选项和明确的退出码。

本章复用前面的 `Agent` 和 `model.open_model()`，自己编写工具、参数解析与 `main()`，不调用仓库的成品 CLI。

## 1. 给入口准备一个可观察的只读工具

新建 `cli_tools.py`：

```python file=cli_tools.py step=minimal

def build_tools(root, calls):
    def list_files():
        calls.append("ListFiles")
        return "\n".join(sorted(path.name for path in root.iterdir()))

    return {"ListFiles": {
        "spec": {"type": "function", "function": {
            "name": "ListFiles", "description": "列出当前工作目录的直接子项。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }},
        "execute": list_files,
        "read_only": True,
    }}
```

这里先只提供 ListFiles。程序运行后，除了看到回答，还能检查模型是否真的使用了这个工具。

## 2. 把参数、组装和显示写进自己的 main

新建 `cli.py`，完整写入：

```python file=cli.py step=minimal
import argparse
from pathlib import Path

from agent import Agent
from cli_tools import build_tools
from model import open_model

DEFAULT_PROMPT = "必须先调用 ListFiles 列出目录，再简要说明结果。"


def parser():
    result = argparse.ArgumentParser(description="我自己写的 Agent")
    result.add_argument("-p", "--prompt", default=DEFAULT_PROMPT)
    result.add_argument("--cwd", type=Path, default=Path.cwd())
    return result


def run(args):
    root = args.cwd.expanduser().resolve()
    if not root.is_dir():
        raise ValueError("工作目录不存在")
    calls = []
    with open_model() as model:
        agent = Agent(model, build_tools(root, calls))
        answer = agent.run(args.prompt)
    if args.prompt == DEFAULT_PROMPT and "ListFiles" not in calls:
        raise RuntimeError("默认任务没有实际列出目录")
    return {"answer": answer, "tool_calls": calls, "cwd": str(root)}


def main(argv=None):
    args = parser().parse_args(argv)
    result = run(args)
    print(result["answer"])
    print("实际工具：", ", ".join(result["tool_calls"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

沿着顺序读：`parser()` 接收任务，`run()` 创建模型与工具并调用 Agent，`main()` 显示结果。工具循环仍在前面亲手写好的 Agent 中。

## 3. 运行最小入口

替换 `demo.py`：

```python file=demo.py step=minimal
from cli import main

raise SystemExit(main())
```

运行 `uv run python demo.py`，应看到真实回答和 `实际工具： ListFiles`。再直接使用自己的 CLI：

```bash
uv run python cli.py --cwd .
uv run python cli.py -p "先列出目录，再说明有哪些 Python 文件"
```

默认任务会检查 ListFiles 是否真实执行。自选任务的完成条件由你定义；不能假定每个任务都必须列目录。

## 4. 增强：JSON 输出与明确的失败出口

现在有了可以工作的入口，再增加 `--json`。新建 `cli_full.py`，复用刚才的解析器和组装函数：

```python file=cli_full.py step=enhanced
import json
import sys

import httpx

from cli import parser, run


def main(argv=None):
    arguments = parser()
    arguments.add_argument("--json", action="store_true", help="输出一行 JSON")
    args = arguments.parse_args(argv)
    try:
        result = run(args)
    except (ValueError, RuntimeError, OSError, httpx.HTTPError) as error:
        print(f"运行失败：{error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["answer"])
        print("实际工具：", ", ".join(result["tool_calls"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

替换 `demo.py`：

```python file=demo.py step=enhanced
from cli_full import main

raise SystemExit(main(["--json"]))
```

运行 `uv run python demo.py`。同一个任务仍调用 ListFiles，但结果变成 JSON。随后运行：

```bash
uv run python cli_full.py --json
uv run python cli_full.py --cwd directory-that-does-not-exist
```

第二条应显示目录错误并以退出码 1 结束，没有发送模型请求。先让这些简单出口清楚，再逐项增加交互输入、会话恢复和流式显示。

## 5. 在自己的项目注册安装入口

将第 02 章的 `pyproject.toml` 替换为下面的完整配置。保留已使用的 httpx 与 jsonschema，再添加构建后端、模块列表和命令入口：

```toml file=pyproject.toml step=enhanced
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "my-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["httpx>=0.28,<1", "jsonschema>=4.23,<5"]

[project.scripts]
my-agent = "cli_full:main"

[tool.setuptools]
py-modules = ["cli", "cli_full", "cli_tools", "agent", "model", "tools"]
```

在自己的项目目录运行：

```bash
uv sync
uv run my-agent --json
```

`uv sync` 安装当前项目并生成命令入口，`uv run` 使用该项目的虚拟环境。如果你给这些模块增加了其他本地导入，也把相应模块加入清单。

本章已经实现了参数读取、组件组装、两种显示方式、失败出口和安装入口。仓库的[最终 CLI](../minicode/cli.py)还组装了会话、权限、Skills、MCP 等功能，作为后续扩展参考；它不是本章 `main()` 背后隐藏调用的程序。
