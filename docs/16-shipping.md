# 第 16 章：从一个 main 函数做成命令行程序

上一章：[评测](15-evals.md) · 下一章：[沙箱](17-sandbox.md)

先让终端中的一句话进入已有 Agent，并把结果打印回来。本章第一版只做这件事，再增加交互、配置选项和安装包。

```bash
uv run python scripts/check_tutorial.py --chapter 16 --export /tmp/minicode-ch16
cd /tmp/minicode-ch16
uv sync
uv run python examples/16_shipping.py --step minimal -p "用 Glob 列出 Python 文件"
```

API 配置沿用[第 00 章](00-setup.md)。完整练习在 [lessons/ch16.py](../lessons/ch16.py)；完整 CLI 的本章版本在 [阶段 cli.py](../lessons/stages/16/minicode/cli.py)。

## 1. 写一个最小入口

把下面的完整程序保存为本章目录中的 `mycli.py`。`open_model()` 复用前面已经建立的真实 API 配置；`--offline` 只用于回归。

```python
import argparse
from pathlib import Path

from lessons.runtime import open_model, print_event
from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.permissions import PermissionRunner
from minicode.testing import call
from minicode.tool import ToolContext, ToolRunner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--prompt", required=True)
    parser.add_argument("--api", default="openai-chat")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    script = [call("Glob", pattern="*.py"), "已列出 Python 文件。"]
    with open_model(args, script) as model:
        tools = build_coding_tools(include_bash=False)
        runner = PermissionRunner(ToolRunner(tools, ToolContext(Path.cwd())), read_only=True)
        for event in Agent(model, runner, max_turns=6).run(args.prompt):
            print_event(event)
            if event["type"] == "result":
                status = event["status"]
    return 0 if status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

运行：

```bash
uv run python mycli.py -p "用 Glob 列出 Python 文件"
```

沿着五步读代码：解析任务 → 创建模型 → 组装工具 → 运行 Agent → 显示事件。CLI 没有复制工具循环。只读执行层沿用第 07 章的实现。

## 2. 先增加一个确实需要的选项

第一次扩展只添加 `--cwd`。在解析参数时加入：

```python
parser.add_argument("--cwd", type=Path, default=Path.cwd())
```

创建 `ToolContext` 时用 `args.cwd.expanduser().resolve()` 代替 `Path.cwd()`。运行同一个 prompt、传入不同目录，观察工具结果如何变化。[最小检查点](../lessons/ch16.py)已经包含这项扩展，也会检查目录是否存在：

```bash
uv run python examples/16_shipping.py --step minimal --cwd . -p "列出 Python 文件"
```

`--api`、`--model` 等参数也按同样方法传给模型配置，不能把服务商判断放进 Agent Loop。

## 3. 增强检查点：接回已有组件

```bash
uv run python examples/16_shipping.py --step enhanced -p "查看项目结构并总结"
```

增强检查点调用本阶段完整 CLI，以只读模式运行同一个任务。现在再读 [cli.py](../lessons/stages/16/minicode/cli.py)，按功能逐组扩展自己的 `main()`：

| 从最小版遇到的需求 | 增加什么 |
| --- | --- |
| 连续输入多个任务 | 输入循环，`exit`/`quit` 结束 |
| 调整连接方式 | 环境变量默认值，命令行覆盖 |
| 显示生成过程 | `--stream` 选择模型流，`--json` 选择显示格式 |
| 保留进度 | `--session` 和 `--resume` 调用已有会话层 |
| 使用已有扩展 | 注册计划、Skill、Delegate 和 MCP 工具 |
| 正常或异常退出都释放资源 | 用 `ExitStack` 管理模型和子进程 |

完整调用方向仍然是：

```text
CLI → 创建 model 与 runner → Agent → 事件显示
          ↓                     ↑
      预算 / 压缩              会话层
```

组装时保留同一个审批对象和工具状态。非交互的 `-p` 没有审批回调，需要写操作时显式配置 `--allow-tool`；`--read-only` 始终优先。Bash 的操作系统隔离在下一章实现。

## 4. 注册可安装的命令

先有可以运行的 `main()`，再在 `pyproject.toml` 声明入口：

```toml
[project.scripts]
minicode = "minicode.cli:main"
```

控制台命令只是调用这个函数。可以从同一个包分别运行：

```bash
uv run python -m minicode --help
uv run minicode --help
uv run minicode --read-only -p "查看项目结构并总结"
```

前面的阶段没有这项控制台声明，所以本章之前的真实实验都使用 Python 入口。

## 5. 最后补配置和退出细节

完整 CLI 区分三类配置：模型连接，Agent 的轮数与预算，应用的显示与扩展。用 `uv run minicode --help` 查阅完整参数，不需要一次记住。

- 密钥从指定环境变量读取，不作为命令行明文参数。
- `--stream` 决定是否请求 SSE；`--json` 决定事件怎样显示。
- 已存在的会话文件需要 `--resume`，缺失的工具结果记为未知，不重做写操作。
- `--provider` 与 `--api` 分开；协议选择发生在模型工厂。
- 成功退出码为 0，任务未完成为 1，配置错误为 2，外层键盘中断为 130。

恢复历史时，应用说明按当前显式配置处理；服务商原始续轮数据只在来源匹配时重用。逐项阅读实现并测试这些条件，不把它们塞回最初的 `main()` 示例。

## 6. 构建并在新环境检查

```bash
uv build
uv venv /tmp/minicode-clean
uv pip install --python /tmp/minicode-clean/bin/python dist/agent_life_from_zero-0.2.0-py3-none-any.whl
/tmp/minicode-clean/bin/minicode --help
/tmp/minicode-clean/bin/minicode --demo
```

这里使用 macOS/Linux 的路径；Windows 对应虚拟环境的 `Scripts` 目录。安装包验证用固定 demo，真实效果继续用前面的 API 任务检查。

## 验收

```bash
uv run python examples/16_shipping.py --step minimal --offline
uv run python examples/16_shipping.py --step enhanced --offline
uv run python -m pytest tests/test_lesson16.py tests/test_app.py tests/test_provider_config.py -q
uv build
```

发布前再从原教程仓库运行全部测试与 `scripts/check_tutorial.py`。给 README 写清安装、真实 API 配置、基本任务、权限与恢复方式。先让别人完成一个任务，再介绍扩展参数。
