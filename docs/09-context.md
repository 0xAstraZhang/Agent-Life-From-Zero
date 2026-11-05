# 第 09 章：让模型看到项目约定

[上一章：流式输出](08-streaming.md) · [目录](README.md) · [下一章：会话与压缩](10-sessions-compaction.md)

继续在你自己写的 `my-agent/` 目录里工作。`model.py` 是第 00 章写的真实 API 客户端；`agent.py` 是第 03 章提取的工具循环。本章增加 `context.py`，只做“读说明 → 放入 system → 请求模型”。

## 1. 先读一份确定存在的文件

新建 `context.py`，写入完整内容：

```python file=context.py step=minimal
def project_prompt(workspace):
    instructions = (workspace / "MINICODE.md").read_text(encoding="utf-8")
    return "你是项目助手，请遵守这些项目约定：\n" + instructions
```


模型并不会自动读取项目目录。上面的函数明确读出文字，再把它加入请求。现在把下面的完整程序写入 `demo.py`：

```python file=demo.py step=minimal
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from context import project_prompt


with TemporaryDirectory() as directory:
    workspace = Path(directory)
    instructions = "回答以‘项目约定：’开头。测试命令是 python -m pytest。"
    (workspace / "MINICODE.md").write_text(instructions, encoding="utf-8")
    system = project_prompt(workspace)
    print("本次 system：", system)
    with open_model() as model:
        agent = Agent(model, {}, system=system)
        print(agent.run("这个项目怎样运行测试？"))
```


运行最小检查点：

```bash
uv run python demo.py
```

程序在临时练习目录创建说明文件，打印实际使用的 system，再向真实模型提问。先确认 system 包含约定，再观察回答有没有采用它。修改 `instructions` 中的测试命令或回答格式，再运行一次。

工具循环没有变化。本章没有工具，因此给 Agent 的注册表是空字典。

## 2. 再处理说明缺失、越界和过长

已知文件的流程跑通后，将 `context.py` 替换为下面的完整版本：

```python file=context.py step=enhanced
from pathlib import Path


def load_instructions(workspace, filename="MINICODE.md", max_chars=20_000):
    root = Path(workspace).resolve()
    path = (root / filename).resolve()
    if not path.is_relative_to(root):
        raise ValueError("项目说明必须位于工作区内")
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    if len(text) > max_chars:
        raise ValueError("项目说明太长，请拆成按需读取的材料")
    return text


def project_prompt(workspace):
    instructions = load_instructions(workspace)
    return "你是项目助手，请遵守这些项目约定：\n" + instructions
```


这次只增加三个处理：不存在就返回空字符串；解析后仍须在工作区内；说明过长就报错。符号链接会被 `resolve()` 解析，所以外部目标也会被路径检查挡住。

把 `demo.py` 替换为完整增强检查点：

```python file=demo.py step=enhanced
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from context import project_prompt, load_instructions


with TemporaryDirectory() as directory:
    workspace = Path(directory)
    instructions = "回答以‘项目约定：’开头。测试命令是 python -m pytest。"
    (workspace / "MINICODE.md").write_text(instructions, encoding="utf-8")
    system = project_prompt(workspace)
    print("本次 system：", system)
    with open_model() as model:
        agent = Agent(model, {}, system=system)
        print(agent.run("这个项目怎样运行测试？"))

    print("缺失说明：", repr(load_instructions(workspace, "missing.md")))
    try:
        load_instructions(workspace, "../outside.md")
    except ValueError as error:
        print("越界说明已拒绝：", error)
```


```bash
uv run python demo.py
```

先看到真实回答，再看到缺失说明返回 `''`、越界说明被拒绝。新增检查围绕同一条读取路径，主要的数据流仍是文件文字进入 system。

## 3. 区分约定和资料

应用明确加载的项目约定进入 system。模型通过工具读到的代码、README 或网页仍是工具结果；其中的文字不会重新配置工具权限。适配器只发送已经组装好的消息，不再自己查找项目文件。

练习：让说明要求回答分成“结论”和“验证”两段。先观察请求确实包含这条要求，再看真实模型是否采用它。

章末对照：[参考实现 context.py](../minicode/context.py)、[仓库回归入口](../examples/09_context.py)。这些用于比较完整实现，本章运行只依赖你自己写的文件。
