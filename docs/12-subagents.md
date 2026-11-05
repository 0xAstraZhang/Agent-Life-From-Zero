# 第 12 章：把一段独立调查交给子 Agent

[上一章：Hooks 与规划](11-hooks-planning.md) · [目录](README.md) · [下一章：Skills](13-skills.md)

主 Agent 需要知道项目怎样测试。给它一个 Delegate 工具，让工具函数里创建另一个 Agent，读完项目记录后把答案返回。先写这条最短路径，再加入工厂和共同预算。

继续使用自己在第 03 章写的 `agent.py`，API 客户端也沿用自己的 `model.py`。

## 1. 在工具函数里运行另一个 Agent

新建 `delegate.py`，写入完整定义：

```python file=delegate.py step=minimal
from agent import Agent


def delegate_tool(child_model, child_tools):
    def investigate(task):
        child = Agent(child_model, child_tools,
                      system="你负责独立调查。先读取项目记录，再给出结论和证据。", max_turns=4)
        return child.run(task)

    return {
        "spec": {"type": "function", "function": {
            "name": "Delegate", "description": "委派一个独立的项目调查。",
            "parameters": {"type": "object", "properties": {"task": {"type": "string"}},
                           "required": ["task"], "additionalProperties": False},
        }},
        "execute": investigate, "read_only": True,
    }
```


每次执行 investigate 都创建一个新 Agent，所以子任务从新的 system 和 task 开始。子 Agent 的最终文本就是这个工具的返回值；父循环继续按普通工具结果记录它。

```text
父 Agent → Delegate(task) → 子 Agent 的独立 history
                           → 读取项目记录
父 Agent ← 工具结果       ← 子 Agent 的最终回答
```

## 2. 先完成一次真正的读取

把下面完整程序写入 `demo.py`。它创建一份临时项目记录，子 Agent 只获得读取这份记录的工具，父 Agent 只获得 Delegate：

```python file=demo.py step=minimal
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from delegate import delegate_tool


with TemporaryDirectory() as directory:
    path = Path(directory) / "project.txt"
    path.write_text("项目：星河\n测试命令：python -m pytest\n", encoding="utf-8")
    reads = []

    def read_note():
        text = path.read_text(encoding="utf-8")
        reads.append(text)
        print("子 Agent 实际读取：", text.strip())
        return text

    child_tools = {"ReadNote": {
        "spec": {"type": "function", "function": {
            "name": "ReadNote", "description": "读取 project.txt 的项目记录。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }},
        "execute": read_note, "read_only": True,
    }}
    with open_model() as model:
        tools = {"Delegate": delegate_tool(model, child_tools)}
        parent = Agent(model, tools)
        print(parent.run("必须用 Delegate 调查测试命令。委派时要求子 Agent 调用 ReadNote，并返回原文中的命令。"))
        returned = [m["content"] for m in parent.history if m["role"] == "tool"]
        if not reads or not any("python -m pytest" in text for text in returned):
            raise RuntimeError("子 Agent 没有完成读取和结果回传")
        print("子 Agent 读取次数：", len(reads))
```


```bash
uv run python demo.py
```

默认都调用真实 API。顺序是父请求委派、子请求读取、子解释结果、父汇总。父子可以使用同一个客户端，决定上下文隔离的是各自的消息列表。

修改 `project.txt` 的内容生成语句，再运行，观察返回的证据随文件变化。委派任务必须写清目标、需要读取的材料和希望得到的结果；不能只说“继续刚才那个问题”。

## 3. 跑通后再提取工厂和共同预算

需要按任务选择不同模型或工具集合时，再将固定对象换成工厂函数。将 `delegate.py` 替换为以下完整版本：

```python file=delegate.py step=enhanced
from agent import Agent


class RequestBudget:
    def __init__(self, limit):
        self.limit = limit
        self.used = 0

    def take(self):
        if self.used >= self.limit:
            raise RuntimeError("父子共享请求预算已用完")
        self.used += 1


class BudgetModel:
    def __init__(self, inner, budget):
        self.inner, self.budget = inner, budget

    def complete(self, messages, tools=()):
        self.budget.take()
        return self.inner.complete(messages, tools)


def delegate_tool(model_factory, tools_factory, budget):
    def investigate(task):
        tools = {name: tool for name, tool in tools_factory().items()
                 if tool.get("read_only", False) and name != "Delegate"}
        model = BudgetModel(model_factory(), budget)
        child = Agent(model, tools,
                      system="你负责独立的只读调查。先读取项目记录，再给出结论和证据。", max_turns=4)
        return child.run(task)

    return {
        "spec": {"type": "function", "function": {
            "name": "Delegate", "description": "委派独立的只读调查，不允许再次委派。",
            "parameters": {"type": "object", "properties": {"task": {"type": "string"}},
                           "required": ["task"], "additionalProperties": False},
        }},
        "execute": investigate, "read_only": True,
    }
```


现在每次委派都从工厂取得依赖，过滤成只读工具，并排除 Delegate，避免嵌套。父模型和子模型引用同一个预算对象，所以每一次真实请求都计入总数。本章串行运行，不需要先引入线程锁或并发调度。

## 4. 运行增强检查点

把 `demo.py` 替换为完整增强版：

```python file=demo.py step=enhanced
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from delegate import delegate_tool, RequestBudget, BudgetModel


with TemporaryDirectory() as directory:
    path = Path(directory) / "project.txt"
    path.write_text("项目：星河\n测试命令：python -m pytest\n", encoding="utf-8")
    reads = []

    def read_note():
        text = path.read_text(encoding="utf-8")
        reads.append(text)
        print("子 Agent 实际读取：", text.strip())
        return text

    child_tools = {"ReadNote": {
        "spec": {"type": "function", "function": {
            "name": "ReadNote", "description": "读取 project.txt 的项目记录。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }},
        "execute": read_note, "read_only": True,
    }}
    with open_model() as model:
        budget = RequestBudget(8)
        tools = {"Delegate": delegate_tool(lambda: model, lambda: child_tools, budget)}
        parent = Agent(BudgetModel(model, budget), tools)
        print(parent.run("必须用 Delegate 调查测试命令。委派时要求子 Agent 调用 ReadNote，并返回原文中的命令。"))
        returned = [m["content"] for m in parent.history if m["role"] == "tool"]
        if not reads or not any("python -m pytest" in text for text in returned):
            raise RuntimeError("子 Agent 没有完成读取和结果回传")
        print("子 Agent 读取次数：", len(reads))

        print("父子合计请求：", budget.used)
```


```bash
uv run python demo.py
```

这次额外打印父子请求总数。完成一次读文件往返通常是四次请求，真实模型也可能需要更多轮；观察计数，不依赖固定措辞。

子任务的工具集合先经过应用筛选，仍沿用第 02 章按名称分发和参数校验的执行器。把这套委派接回更大的应用时，工具工厂继续复用已有权限包装；不要因新增子 Agent 就直接授权写操作。

练习：再增加一个只读记录工具，让子 Agent 比较两份记录。然后降低共享预算，观察父子任意一方用完额度后怎样终止。请求次数是运行上限，不等于费用；token 和计价在后面学习。

章末对照：[仓库子 Agent 完整实现](../lessons/stages/12/minicode/subagents.py)。本章使用的 RequestBudget、BudgetModel 和委派工具都已在正文手写。
