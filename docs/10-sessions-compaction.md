# 第 10 章：先保存对话，再为旧对话制作摘要

[上一章：上下文](09-context.md) · [目录](README.md) · [下一章：Hooks 与规划](11-hooks-planning.md)

继续使用自己写的 `model.py` 和 `agent.py`。本章分开做两个实验：会话保存回答“退出后怎样继续”，摘要回答“下一次请求怎样更短”。先分别跑通，不同时引入。

## 1. 最小会话：保存、加载、继续

我们的历史已经是普通字典列表，可以直接写成 JSON。新建 `session.py`：

```python file=session.py step=minimal
import json


def save_history(path, history):
    path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")


def load_history(path):
    return json.loads(path.read_text(encoding="utf-8"))
```


第 03 章的 Agent 已有 `history` 参数。保存以后创建新的 Agent，把读回的消息交给它即可。把下面完整程序写入 `demo.py`：

```python file=demo.py step=minimal
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from session import save_history, load_history


with TemporaryDirectory() as directory, open_model() as model:
    path = Path(directory) / "session.json"
    first = Agent(model, {})
    print(first.run("记住：项目代号是星河，测试命令是 python -m pytest。"))
    saved = deepcopy(first.history)
    save_history(path, first.history)
    del first

    history = load_history(path)
    if history != saved:
        raise RuntimeError("读回的历史与保存内容不同")
    print("恢复历史：", len(history), "条")
    resumed = Agent(model, {}, history=history)
    print(resumed.run("项目代号和测试命令分别是什么？"))
    if resumed.history[:len(saved)] != saved:
        raise RuntimeError("继续对话时丢失了已有历史")
```


```bash
uv run python demo.py
```

程序先与真实模型对话，把历史写进磁盘，删除第一个 Agent，再从文件恢复。第二个问题没有重述答案；观察新 Agent 如何使用旧消息。

保存的是整个消息字典，所以工具调用、结果 ID 和适配器保留的字段都跟着保存。这里没有另造消息格式。

## 2. 会话增强：写完整再替换，读回时检查格式

将 `session.py` 替换为下面的完整版本。这个练习使用单个写入者；先写临时文件，写完后再替换已有会话：

```python file=session.py step=enhanced
import json


def save_history(path, history):
    record = {"version": 1, "messages": history}
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_history(path):
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("version") != 1:
        raise ValueError("不支持的会话格式")
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("会话必须包含消息")
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ValueError("会话中有不合法的消息")
    return messages
```


增强检查点的完整 `demo.py`：

```python file=demo.py step=enhanced
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from session import save_history, load_history


with TemporaryDirectory() as directory, open_model() as model:
    path = Path(directory) / "session.json"
    first = Agent(model, {})
    print(first.run("记住：项目代号是星河，测试命令是 python -m pytest。"))
    saved = deepcopy(first.history)
    save_history(path, first.history)
    del first

    history = load_history(path)
    if history != saved:
        raise RuntimeError("读回的历史与保存内容不同")
    print("恢复历史：", len(history), "条")
    resumed = Agent(model, {}, history=history)
    print(resumed.run("项目代号和测试命令分别是什么？"))
    if resumed.history[:len(saved)] != saved:
        raise RuntimeError("继续对话时丢失了已有历史")

    save_history(path, resumed.history)
    print("续轮后已保存：", len(load_history(path)), "条")
    path.write_text('{"version": 99, "messages": []}', encoding="utf-8")
    try:
        load_history(path)
    except ValueError as error:
        print("损坏会话已拒绝：", error)
```


```bash
uv run python demo.py
```

先验收正常恢复，再看到不支持的版本被拒绝。真实应用还需要检查调用与结果是否配对。若工具已执行而结果尚未保存，恢复时不能自动重做写操作；这是中断恢复的下一步课题。

## 3. 独立的最小摘要实验

现在不读写会话文件。新建 `compact.py`，把旧的完整用户轮交给模型摘要，保留 system 和最新用户轮：

```python file=compact.py step=compact
import json


def compact_history(model, history):
    latest_user = max(i for i, message in enumerate(history) if message["role"] == "user")
    systems = [m for m in history[:latest_user] if m["role"] == "system"]
    old_turns = [m for m in history[:latest_user] if m["role"] != "system"]
    summary_reply = model.complete([
        {"role": "system", "content": "写一份简短交接摘要，保留目标、约束、结果和未完成事项。"},
        {"role": "user", "content": json.dumps(old_turns, ensure_ascii=False)},
    ])
    summary = summary_reply["message"].get("content", "")
    if summary_reply["finish_reason"] != "stop" or not summary:
        raise ValueError("摘要没有完成")
    summary_message = {"role": "user", "content": "此前任务摘要：\n" + summary}
    return systems + [summary_message] + history[latest_user:]
```


按用户轮切分，是为了让调用与对应结果一起进入摘要，避免留下孤立的工具结果。最新问题保持原文，完整历史也不被覆盖。

将 `demo.py` 替换为以下完整程序：

```python file=demo.py step=compact
from copy import deepcopy

from model import open_model
from agent import Agent
from compact import compact_history


with open_model() as model:
    first = Agent(model, {})
    print(first.run("记住：项目代号是星河，测试命令是 python -m pytest。"))
    history = deepcopy(first.history)
    history.append({"role": "user", "content": "项目代号和测试命令分别是什么？"})
    original = deepcopy(history)
    request = compact_history(model, history)
    print("摘要请求视图：", request)
    reply = model.complete(request)
    if reply["finish_reason"] != "stop":
        raise RuntimeError("模型没有完成回答")
    print(reply["message"]["content"])
    if history != original or request[-1] != original[-1]:
        raise RuntimeError("压缩改动了原历史或最新问题")
    print("完整历史：", len(history), "条；请求视图：", len(request), "条")
```


```bash
uv run python demo.py
```

这次有三次真实请求：记住项目约定、生成摘要、用摘要回答问题。观察打印的请求视图。短对话不一定因摘要变短；这里先看信息怎样流动。

## 4. 摘要增强：达到阈值时才制作视图

理解两份历史后，将 `compact.py` 替换为完整增强版本：

```python file=compact.py step=compact-enhanced
import json


def compact_history(model, history):
    latest_user = max(i for i, message in enumerate(history) if message["role"] == "user")
    systems = [m for m in history[:latest_user] if m["role"] == "system"]
    old_turns = [m for m in history[:latest_user] if m["role"] != "system"]
    summary_reply = model.complete([
        {"role": "system", "content": "写一份简短交接摘要，保留目标、约束、结果和未完成事项。"},
        {"role": "user", "content": json.dumps(old_turns, ensure_ascii=False)},
    ])
    summary = summary_reply["message"].get("content", "")
    if summary_reply["finish_reason"] != "stop" or not summary:
        raise ValueError("摘要没有完成")
    summary_message = {"role": "user", "content": "此前任务摘要：\n" + summary}
    return systems + [summary_message] + history[latest_user:]


class CompactingModel:
    def __init__(self, inner, threshold_chars=48_000):
        self.inner = inner
        self.threshold_chars = threshold_chars
        self.compactions = 0
        self.last_error = ""

    def complete(self, messages, tools=()):
        view = messages
        size = len(json.dumps(messages, ensure_ascii=False))
        user_count = sum(message["role"] == "user" for message in messages)
        if size > self.threshold_chars and user_count >= 2:
            try:
                view = compact_history(self.inner, messages)
                self.compactions += 1
            except ValueError as error:
                self.last_error = str(error)
        return self.inner.complete(view, tools)
```


包装器在请求之前选择视图，再交给原模型。`ValueError` 表示摘要内容没通过检查，此时仍发送完整历史；网络异常继续交给调用方处理。字符阈值只是近似值。

对应的完整 `demo.py`：

```python file=demo.py step=compact-enhanced
from copy import deepcopy

from model import open_model
from agent import Agent
from compact import CompactingModel


with open_model() as raw_model:
    first = Agent(raw_model, {})
    print(first.run("记住：项目代号是星河，测试命令是 python -m pytest。"))
    saved = deepcopy(first.history)
    model = CompactingModel(raw_model, threshold_chars=1)
    resumed = Agent(model, {}, history=first.history)
    print(resumed.run("项目代号和测试命令分别是什么？"))
    if model.compactions != 1 or resumed.history[:len(saved)] != saved:
        raise RuntimeError("摘要未发生，或完整历史被改动")
    print("摘要次数：", model.compactions)
    print("完整历史仍保留：", len(resumed.history), "条")
```


```bash
uv run python demo.py
```

本次将阈值故意设为 1，方便观察一次摘要。实际使用时提高阈值。缓存、单个长任务内的压缩和 token 估算留到第 18 章。

四个检查点都通过同一条 `uv run python demo.py` 命令运行；每次先保存当前版本，再按本节完整代码替换文件。练习：增加一项项目约定，比较磁盘恢复和摘要以后各保留了什么。

章末对照：[完整会话实现](../minicode/session.py)、[本章参考压缩实现](../lessons/stages/10/minicode/compact.py)。主线程序只导入本章及之前自己写的本地模块。
