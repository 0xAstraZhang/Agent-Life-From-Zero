# 第 11 章：在工具前后加回调，再让模型维护计划

[上一章：会话与压缩](10-sessions-compaction.md) · [目录](README.md) · [下一章：子 Agent](12-subagents.md)

继续在自己的 `my-agent/` 中工作。本章先写日志回调，再独立写计划工具。使用的 `execute_call()` 来自第 02 章的 `tools.py`，`permission_runner()` 来自第 07 章，都是你此前写过的本地代码。

## 1. 最小 hook：执行完以后记一条日志

新建 `hooks.py`，写入完整内容：

```python file=hooks.py step=minimal
from tools import execute_call


def after_runner(tools, after):
    def run(call):
        result = execute_call(call, tools)
        after(call, result)
        return result
    return run
```


这个包装函数返回另一个函数。Agent 调用它时，先执行工具，把结果交给 after，再原样返回。没有改变工具调用和历史回传的顺序。

完整 `demo.py` 如下。先用固定项目记录隔离这个实验，不引入文件操作的新问题：

```python file=demo.py step=minimal
from model import open_model
from agent import Agent
from hooks import after_runner


def read_note():
    return "项目使用 python -m pytest 运行测试。"


tools = {"ReadNote": {
    "spec": {"type": "function", "function": {
        "name": "ReadNote", "description": "读取项目测试约定。",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }},
    "execute": read_note,
    "read_only": True,
}}
records = []


def after(call, result):
    records.append((call["function"]["name"], result))


with open_model() as model:
    runner = after_runner(tools, after)
    agent = Agent(model, tools, runner=runner)
    print(agent.run("必须先调用 ReadNote，再说明项目怎样测试。"))
    print("执行日志：", records)
    if not any(name == "ReadNote" for name, result in records):
        raise RuntimeError("没有记录到 ReadNote 的执行")
```


```bash
uv run python demo.py
```

先看真实模型的回答，再看 Python 实际记录的工具名称和结果。日志不是模型说自己执行了什么，而是包装器在执行之后写下的内容。

## 2. 再增加执行前改写和拦截

将 `hooks.py` 替换为完整增强版：

```python file=hooks.py step=enhanced
from copy import deepcopy


def hook_runner(inner, before=None, after=None):
    def run(call):
        current = deepcopy(call)
        if before is not None:
            decision = before(deepcopy(current))
            if isinstance(decision, str):
                return decision
            if decision is not None:
                if not isinstance(decision, dict) or decision.get("id") != call["id"]:
                    return "工具错误：before 必须保留调用 ID"
                current = decision
        result = inner(current)
        if after is not None:
            try:
                after(deepcopy(current), result)
            except Exception as error:
                return result + "\n记录回调失败：" + str(error)
        return result
    return run
```


before 返回 `None` 时保留原调用，返回字符串时直接把它当工具结果，返回调用字典时使用新调用。改写保留调用 ID，且仍要交给原来的权限执行器。

增强检查点先读约定，再尝试写报告。应用拒绝写入，hook 仍能观察到这次调用。把以下完整程序写入 `demo.py`：

```python file=demo.py step=enhanced
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from hooks import hook_runner
from permissions import permission_runner


with TemporaryDirectory() as directory:
    report = Path(directory) / "report.txt"

    def read_note():
        return "项目使用 python -m pytest 运行测试。"

    def write_note():
        report.write_text("测试报告", encoding="utf-8")
        return "报告已写入"

    tools = {}
    for name, function, read_only in [("ReadNote", read_note, True), ("WriteNote", write_note, False)]:
        tools[name] = {
            "spec": {"type": "function", "function": {
                "name": name, "description": "读取项目约定" if read_only else "写入测试报告",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }},
            "execute": function, "read_only": read_only,
        }
    records = []

    def before(call):
        records.append("before:" + call["function"]["name"])
        return None

    def after(call, result):
        records.append("after:" + call["function"]["name"])

    permissions = permission_runner(tools, ask=lambda *_: False)
    runner = hook_runner(permissions, before, after)
    with open_model() as model:
        agent = Agent(model, tools, runner=runner)
        print(agent.run("先调用 ReadNote，再尝试调用 WriteNote，最后说明两次调用的结果。"))
    print("执行日志：", records)
    print("报告没有写入：", not report.exists())
    read_ids = {call["id"] for message in agent.history for call in message.get("tool_calls", [])
                if call["function"]["name"] == "ReadNote"}
    read_ok = any(message["role"] == "tool" and message.get("tool_call_id") in read_ids
                  and "python -m pytest" in message["content"] for message in agent.history)
    if not read_ok or report.exists() or "after:ReadNote" not in records or "after:WriteNote" not in records:
        raise RuntimeError("没有完整观察到读取和写入拒绝")
```


```bash
uv run python demo.py
```

数据流是 `Agent → before → 权限检查 → 工具 → after → Agent`。若 before 把只读调用改成写入，改写后的调用仍经过权限检查。after 收到调用副本和字符串结果；记录失败不会自动重做已经发生的写操作。

## 3. 独立的最小计划：用工具更新一个列表

现在暂时放下 hooks。新建 `planning.py`，写出完整工具定义：

```python file=planning.py step=todo
import json


def plan_tool(state):
    def update(items):
        state[:] = items
        return json.dumps(state, ensure_ascii=False)

    return {
        "spec": {"type": "function", "function": {
            "name": "TodoWrite", "description": "用完整清单更新计划。",
            "parameters": {"type": "object", "properties": {
                "items": {"type": "array", "items": {
                    "type": "object", "properties": {
                        "task": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                    }, "required": ["task", "status"], "additionalProperties": False,
                }},
            }, "required": ["items"], "additionalProperties": False},
        }},
        "execute": update, "read_only": False,
    }
```


核心是 `state[:] = items`：保留同一个列表对象，把内容换成模型提交的新清单。声明告诉模型每项需要任务文字和状态。

完整 `demo.py`：

```python file=demo.py step=todo
from model import open_model
from agent import Agent
from planning import plan_tool
from permissions import permission_runner


state = []
tools = {"TodoWrite": plan_tool(state)}
runner = permission_runner(tools, ask=lambda *_: True)
with open_model() as model:
    agent = Agent(model, tools, runner=runner)
    print(agent.run("调用 TodoWrite，记录两步计划：定位问题、运行测试。第一项进行中，第二项待办。"))
print("实际保存的计划：", state)
if len(state) != 2 or not any(item["status"] == "in_progress" for item in state):
    raise RuntimeError("模型没有写入两步计划")
```


```bash
uv run python demo.py
```

应用明确批准这个只修改练习内存的 TodoWrite 工具。运行后检查真实的 `state`，不要只看模型有没有回答“计划已记录”。

## 4. 计划增强：再加跨条目约束

将 `planning.py` 替换为下面的完整版本，增加“最多一项进行中”的规则：

```python file=planning.py step=todo-enhanced
import json


def plan_tool(state):
    def update(items):
        running = sum(item["status"] == "in_progress" for item in items)
        if running > 1:
            raise ValueError("最多只能有一项 in_progress")
        state[:] = items
        return json.dumps(state, ensure_ascii=False)

    return {
        "spec": {"type": "function", "function": {
            "name": "TodoWrite", "description": "用完整清单更新计划。",
            "parameters": {"type": "object", "properties": {
                "items": {"type": "array", "items": {
                    "type": "object", "properties": {
                        "task": {"type": "string"},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "done"]},
                    }, "required": ["task", "status"], "additionalProperties": False,
                }},
            }, "required": ["items"], "additionalProperties": False},
        }},
        "execute": update, "read_only": False,
    }
```


使用以下完整 `demo.py` 再运行一次：

```python file=demo.py step=todo-enhanced
from model import open_model
from agent import Agent
from planning import plan_tool
from permissions import permission_runner


state = []
tools = {"TodoWrite": plan_tool(state)}
runner = permission_runner(tools, ask=lambda *_: True)
with open_model() as model:
    agent = Agent(model, tools, runner=runner)
    print(agent.run("调用 TodoWrite，记录两步计划：定位问题、运行测试。第一项进行中，第二项待办。"))
print("实际保存的计划：", state)
if len(state) != 2 or not any(item["status"] == "in_progress" for item in state):
    raise RuntimeError("模型没有写入两步计划")
```


```bash
uv run python demo.py
```

参数形状由第 02 章的执行器校验；这次新增的约束比较多个条目。违反约束时函数抛出 `ValueError`，执行器把错误交还模型，模型可以提交修正后的清单。

计划中的 `done` 只表示模型更新了状态。是否真的完成，要看文件和测试结果。只读计划模式则是另一件事：第 07 章的权限执行器可以禁用写工具，它不会因为提示词里说“规划”就自动改变。

练习：回到第 2 节的 hooks 检查点，给 after 增加耗时记录；再回到第 4 节的计划检查点，让模型把第二项改成进行中，观察真实列表变化。

章末对照：[完整 hooks 实现](../minicode/hooks.py)、[完整规划实现](../minicode/planning.py)。本章所有可运行代码已在正文给出。
