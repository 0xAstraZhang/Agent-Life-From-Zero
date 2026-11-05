# 第 01 章：把一次聊天变成 Agent 循环

上一章：[第一次真实对话](00-setup.md) · [目录](README.md) · 下一章：[工具注册表](02-tools.md)

这一章让模型调用一个加法函数，再根据函数结果回答。你只需要在上一章的消息列表里增加两种记录，就能看见 Agent 的骨架：

```text
请求模型 → 模型提出工具调用 → Python 执行 → 把结果放回历史 → 再请求模型
```

先用纯计算工具看清这个循环。文件路径、写入权限和命令执行留到编码工具章节。

## 1. 准备本章目录

沿用第 00 章的 API 配置。模型需要支持 Chat Completions 的函数工具调用。完整文件是 [lessons/ch01.py](../lessons/ch01.py)，只复用上一章的 HTTP 客户端。

如果需要独立练习目录，在仓库根目录执行：

```bash
uv run python scripts/check_tutorial.py --chapter 1 --export /tmp/minicode-ch01
cd /tmp/minicode-ch01
uv sync
```

导出目标须为空。接下来的运行命令在仓库根目录或导出的目录中执行。

## 2. 告诉模型这里有一个工具

工具声明描述函数名称、用途和输入格式，不包含 Python 函数本身：

```python
ADD = {"type": "function", "function": {
    "name": "add", "description": "计算两个整数的和",
    "parameters": {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False},
}}
```

把声明传给 `model.complete(messages, [ADD])`。模型可能返回这样的助手消息：

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [{
    "id": "call_1",
    "type": "function",
    "function": {"name": "add", "arguments": "{\"a\":2,\"b\":3}"}
  }]
}
```

它表示“请调用 add”，此时加法还没有发生。`arguments` 是 JSON 字符串，Python 需要先解析它：

```python
import json


def add(request):
    arguments = json.loads(request["function"]["arguments"])
    return str(arguments["a"] + arguments["b"])
```

## 3. 最小原型：把调用与结果放回历史

下面的检查点可复制运行。`ADD` 和 `add` 就是前面两个定义，HTTP 客户端来自第 00 章。

```python
# 可运行检查点：01-minimal
from lessons.ch00 import open_chat
from lessons.ch01 import ADD, add


def agent_loop(model, prompt):
    messages = [{"role": "user", "content": prompt}]
    for turn in range(4):
        reply = model.complete(messages, [ADD])
        message = reply["message"]
        calls = message.get("tool_calls") or []
        if reply["finish_reason"] == "stop" and not calls:
            return message.get("content") or ""
        if reply["finish_reason"] != "tool_calls":
            raise RuntimeError("模型回复尚未完整结束")
        messages.append(message)
        for request in calls:
            result = add(request)
            messages.append({
                "role": "tool", "tool_call_id": request["id"], "content": result,
            })
    raise RuntimeError("已达到模型请求轮数上限")


with open_chat() as model:
    print(agent_loop(model, "请调用 add 工具计算 2 加 3，再告诉我结果。"))
```

也可以运行同一步的完整版本：

```bash
uv run python examples/01_agent_loop.py --step minimal
```

观察工具结果 `5` 和模型最后的回答。沿历史理解这段程序：

1. 第一次请求带上用户问题和工具声明。
2. 将模型的原始调用追加到历史。
3. Python 计算，并把结果关联到同一个 `tool_call_id`。
4. 第二次请求带上这三条记录，模型据此回答。

```text
user: 请计算 2 + 3
assistant: add(a=2, b=3)，ID=call_1
tool: 5，对应 call_1
assistant: 2 + 3 = 5
```

工具结果和最终回答是两个不同的步骤。只执行 `add()` 然后打印 `5`，还没有完成“模型读取工具结果”的往返。

这里的轮数上限防止一直请求模型；`finish_reason` 区分完整回答、完整工具调用和被截断等未完成状态。最小版本遇到未完成回复就停止，不执行其中的调用。

## 4. 跑通以后：错误也可以成为下一轮的输入

现在再制造问题：模型把 `a` 写成字符串 `"2"`。我们希望本地执行器返回“参数应为整数”，然后让模型修正参数。

完整文件里的 `add_checked()` 在执行前检查工具名和参数，把错误变成普通工具结果。`check_reply()` 则检查模型回复自身是否矛盾。两个不一致情况分别写清楚：

```python
if reason == "tool_calls" and not calls:
    raise ValueError("模型要求调用工具，但没有提供工具调用")
if reason == "stop" and calls:
    raise ValueError("模型声明回答结束，但仍包含工具调用")
```

前者缺少动作，后者同时声称结束和继续。这些校验放在执行之前；教学上是在理解正常循环以后再加入。

增强检查点沿用同一个循环，开启上述检查：

```python
# 可运行检查点：01-enhanced
from lessons.ch00 import open_chat
from lessons.ch01 import run

with open_chat() as model:
    answer, history = run(model, "请调用 add 计算 2 加 3，再回答。", enhanced=True)
    print(answer)
```

```bash
uv run python examples/01_agent_loop.py --step enhanced
```

真实模型不一定犯参数错误。要稳定观察“错误结果 → 修正调用 → 回答”，再使用离线检查夹具：

```bash
uv run python examples/01_agent_loop.py --offline --step enhanced
uv run python -m pytest tests/test_ch01.py -q
```

测试检查第二次 HTTP 请求中的调用和结果是否匹配、参数错误是否回传、截断时是否停止、请求是否受到轮数限制。固定回复只用来制造和复现这些条件。

## 5. 下一步为什么需要工具注册表

现在只有 `add()`，循环直接调用它。再加入乘法时，我们就需要根据模型返回的名称找到对应函数。下一章先增加这个能力，再把重复的参数检查集中到一处。

练习：换两个数字运行；打印第二次请求的历史，指出是哪条消息让模型知道 Python 算出了什么。

工具协议参见 [Function calling 官方说明](https://developers.openai.com/api/docs/guides/function-calling)。
