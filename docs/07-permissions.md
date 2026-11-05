# 第 7 章：自己写一个工具审批函数

[上一章：编码工具](06-coding-tools.md) · [目录](README.md) · [下一章：流式输出](08-streaming.md)

继续自己的 `my-agent` 项目。你已经会把模型请求交给 Python 函数执行，本章在这一步前插入用户决定。第 03 章的 Agent 接受一个可选 `runner`，因此无需重新写循环。

## 1. 先实现一次批准或拒绝

新建 `permissions.py`。`execute_call()` 是你第 02 章写在 `tools.py` 中的执行函数；现在只在它前面加一个条件：

```python file=permissions.py step=minimal
import json

from tools import execute_call


def permission_runner(tools, ask):
    def run(call):
        name = call["function"]["name"]
        arguments = json.loads(call["function"]["arguments"])
        if ask(name, arguments):
            return execute_call(call, tools)
        return "用户拒绝此次操作，工具没有执行；不要换工具绕过拒绝。"
    return run
```

同意时执行工具，拒绝时返回一段结果。两个分支都回到原来的 Agent，仍由它使用调用 ID 将结果写入历史。模型下一次请求便能知道这次操作有没有发生。

## 2. 让真实模型发起一次写入

完整替换 `demo.py`，定义一个只创建临时练习文件的工具：

```python file=demo.py step=minimal
from pathlib import Path
from tempfile import TemporaryDirectory

from agent import Agent
from model import open_model
from permissions import permission_runner


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "note.txt"

        def write_note(content):
            path.write_text(content, encoding="utf-8")
            return "已创建 note.txt"

        tools = {"WriteNote": {
            "spec": {"type": "function", "function": {
                "name": "WriteNote", "description": "创建练习文件 note.txt",
                "parameters": {"type": "object", "properties": {"content": {"type": "string"}},
                               "required": ["content"], "additionalProperties": False}}},
            "execute": write_note, "read_only": False,
        }}
        decisions = []

        def ask(name, arguments):
            print("模型请求：", name, arguments)
            allowed = input("允许创建练习文件？[y/N] ").strip().lower() == "y"
            decisions.append(allowed)
            return allowed

        runner = permission_runner(tools, ask)
        with open_model() as model:
            agent = Agent(model, tools, runner=runner)
            print(agent.run("请调用 WriteNote 写入 hello，再依据工具结果回答；拒绝后不要重试。"))
        if not decisions:
            raise RuntimeError("模型没有提出需要审批的调用")
        if path.exists() != any(decisions):
            raise RuntimeError("文件状态与审批决定不一致")
        if path.exists() and path.read_text(encoding="utf-8") != "hello":
            raise RuntimeError("文件内容不正确")
        print("文件存在：", path.exists())


if __name__ == "__main__":
    main()
```

运行：

```bash
uv run python demo.py
```

第一次输入 `y`，确认文件被创建；第二次输入 `n`，确认文件不存在。观察模型最后的回答，再检查历史中相同调用 ID 对应的工具结果。

这时已经完成本章的骨架：请求 → 询问 → 执行或拒绝 → 结果回传 → 再请求模型。权限依据用户输入，模型自行声称“用户已同意”不会改变这个决定。

## 3. 多个工具出现后，再区分只读与写入

读取文件通常不需要每次询问。为工具增加本地的 `read_only` 标记，并加入整体只读模式。完整替换 `permissions.py`：

```python file=permissions.py step=enhanced
import json

from tools import execute_call


def permission_runner(tools, ask, read_only=False):
    def run(call):
        name = call["function"]["name"]
        tool = tools.get(name)
        if tool is None:
            return execute_call(call, tools)
        try:
            arguments = json.loads(call["function"]["arguments"])
        except (TypeError, ValueError):
            return execute_call(call, tools)
        if not isinstance(arguments, dict):
            return execute_call(call, tools)
        if read_only and not tool.get("read_only", False):
            return "只读模式不允许执行这个工具。"
        if tool.get("read_only", False):
            return execute_call(call, tools)
        if ask(name, arguments):
            return execute_call(call, tools)
        return "用户拒绝此次操作，工具没有执行；不要换工具绕过拒绝。"
    return run
```

先用正常调用理解三条规则：只读工具直接执行；写入工具询问；只读模式拒绝写入。未知工具或无法解析的参数仍交回已有执行函数处理，不把这些错误解释成用户授权。

`read_only` 是你给工具函数设置的属性，不读取模型返回的同名文字。拒绝必须发生在执行入口；仅在提示词中告诉模型“别写文件”不能代替这个判断。

## 4. 观察不需要询问的读取

完整替换 `demo.py`，新增 `ReadNote`：

```python file=demo.py step=enhanced
from pathlib import Path
from tempfile import TemporaryDirectory

from agent import Agent
from model import open_model
from permissions import permission_runner


def main():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "note.txt"

        def write_note(content):
            path.write_text(content, encoding="utf-8")
            return "已创建 note.txt"

        tools = {"WriteNote": {
            "spec": {"type": "function", "function": {
                "name": "WriteNote", "description": "创建练习文件 note.txt",
                "parameters": {"type": "object", "properties": {"content": {"type": "string"}},
                               "required": ["content"], "additionalProperties": False}}},
            "execute": write_note, "read_only": False,
        }}
        tools["ReadNote"] = {
            "spec": {"type": "function", "function": {
                "name": "ReadNote", "description": "读取练习文件，或说明尚未创建",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
            "execute": lambda: path.read_text(encoding="utf-8") if path.exists() else "尚未创建",
            "read_only": True,
        }
        decisions = []

        def ask(name, arguments):
            print("模型请求：", name, arguments)
            allowed = input("允许创建练习文件？[y/N] ").strip().lower() == "y"
            decisions.append(allowed)
            return allowed

        runner = permission_runner(tools, ask)
        with open_model() as model:
            agent = Agent(model, tools, runner=runner)
            print(agent.run("先 ReadNote，再调用 WriteNote 写入 hello，依据工具结果回答；拒绝后不要重试。"))
        if not decisions:
            raise RuntimeError("模型没有提出需要审批的调用")
        if path.exists() != any(decisions):
            raise RuntimeError("文件状态与审批决定不一致")
        if path.exists() and path.read_text(encoding="utf-8") != "hello":
            raise RuntimeError("文件内容不正确")
        print("文件存在：", path.exists())


if __name__ == "__main__":
    main()
```

再运行 `uv run python demo.py`。ReadNote 直接返回“尚未创建”，WriteNote 才询问用户。这个检查点可以输入 `n`，观察拒绝结果进入下一次模型请求。

接着把构造语句改为 `permission_runner(tools, ask, read_only=True)`。模型即使请求 WriteNote，也应该直接收到只读拒绝。此时应相应调整入口末尾“必须出现询问”的实验断言，因为你正在验证另一种模式。

## 5. 需要更多规则时再增加

先用上面的闭环验证审批，再考虑允许名单、参数校验后再询问、多个 Agent 共享终端时的审批锁。后续包装器继续接收原始工具调用并返回字符串，可以叠加在 `permission_runner()` 外层。

章末可选参考：[仓库完整权限执行器](../minicode/permissions.py)、[仓库实验](../examples/07_permissions.py)。它们不参与当前手写程序的运行。
