# 第 01 章：亲手写出 Agent 循环

上一章：[真实模型对话](00-setup.md) · [目录](README.md) · 下一章：[工具注册](02-tools.md)

继续使用你自己的 `my-agent` 目录。上一章已经亲手写了 `model.py`：它负责发送消息。这一章新建 `agent.py`，让模型提出一个加法动作，由 Python 执行，再把结果交给模型。

```text
请求模型 → 得到工具调用 → Python 执行 → 结果加入历史 → 再请求模型
```

## 1. 在新文件里写声明、函数和循环

下面是 `agent.py` 的完整第一版。先读 `ADD` 和 `add()`，再读 `run()`；它们都在这个文件中定义。

```python file=agent.py step=minimal
import json

ADD = {"type": "function", "function": {
    "name": "add",
    "description": "计算两个整数的和",
    "parameters": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
}}


def add(request):
    arguments = json.loads(request["function"]["arguments"])
    return str(arguments["a"] + arguments["b"])


def run(model, prompt, max_turns=4):
    messages = [{"role": "user", "content": prompt}]
    for turn in range(max_turns):
        reply = model.complete(messages, [ADD])
        message = reply["message"]
        calls = message.get("tool_calls") or []
        messages.append(message)
        if reply["finish_reason"] == "stop" and not calls:
            return message.get("content") or ""
        if reply["finish_reason"] != "tool_calls":
            raise RuntimeError("模型回复未完整结束")
        for request in calls:
            result = add(request)
            print("工具结果：", result)
            messages.append({"role": "tool", "tool_call_id": request["id"], "content": result})
    raise RuntimeError("已达到模型请求轮数上限")
```

`ADD` 是给模型看的工具说明，`add()` 才是真正执行加法的 Python 函数。模型返回的 `arguments` 是 JSON 字符串，先解析成字典，再取出数字。

循环中的正常流程只有几步：

1. 把用户消息和工具声明交给模型。
2. 保存模型的原始调用消息。
3. Python 调用 `add()`，把结果放进一条 `tool` 消息。
4. 用 `tool_call_id` 标明它回答的是哪次调用，再请求模型。
5. 模型返回完整文本时结束。

轮数上限防止无休止请求。未完成的回复直接停止，避免执行半截调用。更详细的错误处理放到本章第二步。

把 `demo.py` 替换为下面的运行入口。这里两个导入都来自你已经写出的文件：

```python file=demo.py step=minimal
from model import open_model
from agent import run

with open_model() as model:
    print(run(model, "请调用 add 计算 2 加 3，再告诉我结果。"))
```

```bash
uv run python demo.py
```

你应先看到本地工具结果 `5`，再看到模型给出的最终回答。只打印 Python 算出的 `5` 还不算完成工具往返；第二次模型请求必须包含调用和结果。

## 2. 跑通以后，再让错误返回给模型

如果模型把 `a` 写成字符串，直接加法会报错。我们希望把“参数应为整数”作为工具结果交还模型，让它有机会修正。此时再加入 `add_checked()` 与 `check_reply()`。

将 `agent.py` 替换成下面的增强版。改动是增加两个函数，在模型回复后检查状态，在工具执行处调用 `add_checked()`；主循环的顺序没有变。

```python file=agent.py step=enhanced
import json

ADD = {"type": "function", "function": {
    "name": "add",
    "description": "计算两个整数的和",
    "parameters": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
}}


def add(request):
    arguments = json.loads(request["function"]["arguments"])
    return str(arguments["a"] + arguments["b"])


def add_checked(request):
    try:
        if request["function"]["name"] != "add":
            raise ValueError("未知工具")
        arguments = json.loads(request["function"]["arguments"])
        if not isinstance(arguments, dict) or set(arguments) != {"a", "b"}:
            raise ValueError("add 需要 a 和 b 两个参数")
        if type(arguments["a"]) is not int or type(arguments["b"]) is not int:
            raise ValueError("a 和 b 必须是整数")
        return str(arguments["a"] + arguments["b"])
    except (ValueError, TypeError, KeyError) as error:
        return f"工具错误：{error}"


def check_reply(reply):
    calls = reply["message"].get("tool_calls") or []
    if reply["finish_reason"] == "tool_calls" and not calls:
        raise ValueError("模型要求调用工具，但没有给出调用")
    if reply["finish_reason"] == "stop" and calls:
        raise ValueError("模型声明回答结束，但仍包含调用")


def run(model, prompt, max_turns=4):
    messages = [{"role": "user", "content": prompt}]
    for turn in range(max_turns):
        reply = model.complete(messages, [ADD])
        check_reply(reply)
        message = reply["message"]
        calls = message.get("tool_calls") or []
        messages.append(message)
        if reply["finish_reason"] == "stop" and not calls:
            return message.get("content") or ""
        if reply["finish_reason"] != "tool_calls":
            raise RuntimeError("模型回复未完整结束")
        for request in calls:
            result = add_checked(request)
            print("工具结果：", result)
            messages.append({"role": "tool", "tool_call_id": request["id"], "content": result})
    raise RuntimeError("已达到模型请求轮数上限")
```

```python file=demo.py step=enhanced
from model import open_model
from agent import run

with open_model() as model:
    print(run(model, "请调用 add 计算 2 加 3，再告诉我结果。"))
```

再次运行 `uv run python demo.py`。真实模型可能一次就给对参数；可以临时把 `add_checked()` 中的参数改成错误类型来观察错误反馈，然后恢复。

工具参数错误和模型回复矛盾是两回事：前者可以交还模型修正，后者没有可靠的执行指令，应停止。两个一致性判断分别写开，避免读者先反推布尔表达式。

## 3. 验收

打印第二次请求前的 `messages`，应看到 `user → assistant(tool_calls) → tool`，调用与结果的 ID 相同。接着改两个数字再运行。

下一章增加乘法。如果继续在循环中硬编码函数名，每添一个工具都要改循环。工具注册表正是为解决这个已经出现的问题而提取的。

源码对照可以在完成手写之后再看 [lessons/ch01.py](../lessons/ch01.py)。这份参考不是上面程序的依赖。
