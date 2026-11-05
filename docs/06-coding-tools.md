# 第 6 章：自己写出读、改、检查三个工具

[上一章：把循环提取为 Agent](03-chat-completions.md) · [目录](README.md) · [下一章：权限](07-permissions.md)

继续在自己的 `my-agent` 目录中写。第 03 章已经定义了 `Agent` 和工具注册表的格式；这章给它三个新的 Python 函数。协议选修章节可以稍后再读，本章仍使用你最初接通的 Chat Completions API。

## 1. 先写一个固定文件的工具

新建 `coding_tools.py`，完整写入下面的代码。先只操作 `answer.txt`，这样注意力可以放在“模型如何利用工具结果继续行动”上：

```python file=coding_tools.py step=minimal
from pathlib import Path


def define_tool(name, description, execute, properties, read_only=False):
    return {
        "spec": {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False}}},
        "execute": execute, "read_only": read_only,
    }


def build_coding_tools(root):
    path = Path(root) / "answer.txt"

    def read():
        return path.read_text(encoding="utf-8")

    def edit(old, new):
        text = read()
        if not old or text.count(old) != 1:
            raise ValueError("old 必须恰好匹配一处")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return "修改完成"

    def check():
        return "检查通过" if read().strip() == "42" else "检查失败：内容应为 42"

    return {
        "Read": define_tool("Read", "读取 answer.txt", read, {}, True),
        "Edit": define_tool("Edit", "修改 answer.txt 中的一处文本", edit,
                            {"old": {"type": "string"}, "new": {"type": "string"}}),
        "RunTests": define_tool("RunTests", "检查 answer.txt 是否为 42", check, {}, True),
    }
```

先看三个内层函数：Read 返回文件内容，Edit 执行一次替换，RunTests 重新读取文件并检查结果。`define_tool()` 只把函数和第 02 章已有的工具说明格式放到一起。

Edit 保留唯一匹配判断，因为“替换哪一处”直接决定这个工具的含义。路径选择、版本记录和搜索还没有参与这个最小任务。

## 2. 创建练习文件，交给真实模型

完整替换 `demo.py`。这里导入的 `Agent` 和 `open_model()` 都是你前章已写好的本地文件：

```python file=demo.py step=minimal
from pathlib import Path
from tempfile import TemporaryDirectory

from agent import Agent
from coding_tools import build_coding_tools
from model import open_model


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "answer.txt"
        path.write_text("41\n", encoding="utf-8")
        tools = build_coding_tools(root)
        with open_model() as model:
            agent = Agent(model, tools)
            print(agent.run("先 Read answer.txt，再 Edit 将 41 改成 42，最后 RunTests。"))
        names = [call["function"]["name"] for message in agent.history
                 for call in message.get("tool_calls", [])]
        if not all(name in names for name in ("Read", "Edit", "RunTests")):
            raise RuntimeError("模型尚未实际完成读、改、检查")
        text = path.read_text(encoding="utf-8").strip()
        if text != "42":
            raise RuntimeError("最终文件内容不正确")
        if not any(message["role"] == "tool" and message["content"] == "检查通过"
                   for message in agent.history):
            raise RuntimeError("没有取得检查通过的工具结果")
        print("最终文件：", text)


if __name__ == "__main__":
    main()
```

运行第一个检查点：

```bash
uv run python demo.py
```

你应看到模型的任务回答，以及 Python 独立读出的 `最终文件：42`。临时目录结束时清理；模型只能修改其中的固定文件，也不能输入 shell 命令。

检查 `agent.history`：Read 后出现文件内容，Edit 后出现修改结果，RunTests 后出现检查结果。只有结果再次进入模型请求，模型才知道上一个动作发生了什么。程序末尾的检查根据实际文件和工具结果判断，不以模型说“完成了”作为通过条件。

## 3. 需要选择文件时，再加路径和版本

现在让 Read、Edit 接受 `path`。当模型能指定路径，就需要定义工作目录；当文件可能在读后被修改，就需要核对版本。完整替换 `coding_tools.py`：

```python file=coding_tools.py step=enhanced
import hashlib
from pathlib import Path


def define_tool(name, description, execute, properties, read_only=False):
    return {
        "spec": {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False}}},
        "execute": execute, "read_only": read_only,
    }


def build_coding_tools(root):
    root = Path(root).resolve()
    seen = {}

    def resolve(path):
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise ValueError("文件必须位于练习目录")
        return target

    def read(path):
        target = resolve(path)
        data = target.read_bytes()
        seen[target] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8")

    def edit(path, old, new):
        target = resolve(path)
        data = target.read_bytes()
        if target not in seen:
            raise ValueError("修改前先 Read")
        if seen[target] != hashlib.sha256(data).hexdigest():
            raise ValueError("文件已经变化，请重新 Read")
        text = data.decode("utf-8")
        if not old or text.count(old) != 1:
            raise ValueError("old 必须恰好匹配一处")
        updated = text.replace(old, new, 1).encode("utf-8")
        target.write_bytes(updated)
        seen[target] = hashlib.sha256(updated).hexdigest()
        return "修改完成"

    def check():
        text = (root / "answer.txt").read_text(encoding="utf-8")
        return "检查通过" if text.strip() == "42" else "检查失败：内容应为 42"

    return {
        "Read": define_tool("Read", "读取练习目录内的文件", read,
                            {"path": {"type": "string"}}, True),
        "Edit": define_tool("Edit", "修改已读文件中的一处文本", edit,
                            {"path": {"type": "string"}, "old": {"type": "string"},
                             "new": {"type": "string"}}),
        "RunTests": define_tool("RunTests", "检查 answer.txt 是否为 42", check, {}, True),
    }
```

新增逻辑都能对应到原来的一个动作：`resolve()` 决定访问哪个文件，`seen` 记录 Read 时的内容摘要，Edit 比较摘要后才修改。它们是在已有工具上补充规则。

## 4. 用相同任务检查增强版

`demo.py` 的任务无需改变；现在模型要在 Read、Edit 中给出 `path="answer.txt"`。本检查点完整入口如下：

```python file=demo.py step=enhanced
from pathlib import Path
from tempfile import TemporaryDirectory

from agent import Agent
from coding_tools import build_coding_tools
from model import open_model


def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "answer.txt"
        path.write_text("41\n", encoding="utf-8")
        tools = build_coding_tools(root)
        with open_model() as model:
            agent = Agent(model, tools)
            print(agent.run("先 Read answer.txt，再 Edit 将 41 改成 42，最后 RunTests。"))
        names = [call["function"]["name"] for message in agent.history
                 for call in message.get("tool_calls", [])]
        if not all(name in names for name in ("Read", "Edit", "RunTests")):
            raise RuntimeError("模型尚未实际完成读、改、检查")
        text = path.read_text(encoding="utf-8").strip()
        if text != "42":
            raise RuntimeError("最终文件内容不正确")
        if not any(message["role"] == "tool" and message["content"] == "检查通过"
                   for message in agent.history):
            raise RuntimeError("没有取得检查通过的工具结果")
        print("最终文件：", text)


if __name__ == "__main__":
    main()
```

再次运行 `uv run python demo.py`。先确认正常任务仍能完成，再逐个尝试失败条件：不 Read 就 Edit；Read 后在程序外修改文件；请求 `../` 指向的外部文件；`old` 匹配零处或多处。每次只改变一个条件，观察工具结果如何影响下一轮。

## 5. 后续怎样扩展

当前只提供 Read、Edit 和固定 RunTests。需要创建文件时再写 Write，需要找文件时再写 Glob/Grep，需要运行任意项目命令时再写 Bash。每新增一个函数，先给模型一个能观察效果的小任务，再讨论输出限制、超时和进程清理。

通用 Bash 可以访问工作目录以外的内容，固定 RunTests 没有这个能力。下一章先学习审批，第 17 章再学习操作系统沙箱。

章末可选参考：[仓库完整编码工具](../lessons/stages/06/minicode/coding_tools.py)、[仓库实验](../examples/06_coding_tools.py)。这些参考文件不需要复制或导入到当前读者项目。
