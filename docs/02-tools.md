# 第 02 章：增加第二个工具，再提取注册表

上一章：[Agent 循环](01-agent-loop.md) · [目录](README.md) · 下一章：[可复用的 Agent](03-chat-completions.md)

继续编辑自己的 `my-agent`。本章新增 `tools.py`，把工具声明和函数放在同一条记录里。`model.py` 保持上一章的版本。

## 1. 写出工具注册表

新建 `tools.py`，先只处理正确输入：

```python file=tools.py step=minimal
import json

INTEGER_PAIR = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}


def add(a, b):
    return a + b


def multiply(a, b):
    return a * b


def entry(name, description, execute):
    return {
        "spec": {"type": "function", "function": {
            "name": name, "description": description, "parameters": INTEGER_PAIR,
        }},
        "execute": execute,
        "read_only": True,
    }


TOOLS = {
    "add": entry("add", "计算两个整数的和", add),
    "multiply": entry("multiply", "计算两个整数的积", multiply),
}


def execute_call(request, tools):
    function = request["function"]
    tool = tools[function["name"]]
    arguments = json.loads(function["arguments"])
    return str(tool["execute"](**arguments))
```

`spec` 发给模型，`execute` 留在 Python 中。`entry()` 只是将重复的字典写法集中起来，它的全部实现就在上面。

现在把 `agent.py` 替换成下面的完整版本。与第一章相比，只把工具声明改成一个列表，将 `add(request)` 改成 `execute_call(request, tools)`：

```python file=agent.py step=minimal
from tools import execute_call


def run(model, prompt, tools, max_turns=6):
    messages = [{"role": "user", "content": prompt}]
    specs = [tool["spec"] for tool in tools.values()]
    for turn in range(max_turns):
        reply = model.complete(messages, specs)
        message = reply["message"]
        calls = message.get("tool_calls") or []
        messages.append(message)
        if reply["finish_reason"] == "stop" and not calls:
            return message.get("content") or ""
        if reply["finish_reason"] != "tool_calls" or not calls:
            raise RuntimeError("模型没有返回完整回答或完整调用")
        for request in calls:
            result = execute_call(request, tools)
            print("工具结果：", result)
            messages.append({"role": "tool", "tool_call_id": request["id"], "content": result})
    raise RuntimeError("已达到模型请求轮数上限")
```

把 `demo.py` 替换为：

```python file=demo.py step=minimal
from model import open_model
from agent import run
from tools import TOOLS

with open_model() as model:
    print(run(model, "先调用 add 计算 2 加 3，再用 multiply 将结果乘以 4。", TOOLS))
```

```bash
uv run python demo.py
```

观察两次本地结果 `5`、`20`，再看模型回答。此时增加 `subtract()` 并登记进 `TOOLS`，就不需要修改 `run()`。

## 2. 在执行前加入参数校验

等两个工具都能运行，再处理无效参数。工具声明已经写出参数形状，可以直接用 JSON Schema 校验。先替换项目配置并运行 `uv sync`：

```toml file=pyproject.toml step=enhanced
[project]
name = "my-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["httpx>=0.28,<1", "jsonschema>=4.23,<5"]
```

把 `tools.py` 替换为下面的增强版。注册表保持原样，改变的部分集中在 `execute_call()`：

```python file=tools.py step=enhanced
import json
from jsonschema import Draft202012Validator

INTEGER_PAIR = {
    "type": "object",
    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
    "required": ["a", "b"],
    "additionalProperties": False,
}


def add(a, b):
    return a + b


def multiply(a, b):
    return a * b


def entry(name, description, execute):
    return {
        "spec": {"type": "function", "function": {
            "name": name, "description": description, "parameters": INTEGER_PAIR,
        }},
        "execute": execute,
        "read_only": True,
    }


TOOLS = {
    "add": entry("add", "计算两个整数的和", add),
    "multiply": entry("multiply", "计算两个整数的积", multiply),
}


def execute_call(request, tools):
    function = request["function"]
    if function["name"] not in tools:
        return "工具错误：未知工具 " + function["name"]
    tool = tools[function["name"]]
    try:
        arguments = json.loads(function["arguments"])
        Draft202012Validator(tool["spec"]["function"]["parameters"]).validate(arguments)
        return str(tool["execute"](**arguments))
    except Exception as error:
        return f"工具错误：{error}"
```

```python file=demo.py step=enhanced
from model import open_model
from agent import run
from tools import TOOLS

with open_model() as model:
    print(run(model, "先调用 add 计算 2 加 3，再用 multiply 将结果乘以 4。", TOOLS))
```

再次运行 `uv run python demo.py`。执行顺序是：查工具 → 解析参数 → 校验 → 调用函数。未知工具、错误参数和工具函数抛出的普通异常都返回一段错误文字，循环仍把它作为原调用的结果交还模型。后面接文件工具时，读取失败也走同一条路径；模型请求发生在执行器之外。

可以先让模型完成正常任务，再临时给 `execute_call()` 的 `arguments` 放入 `{"a":"2","b":3}`，确认计算函数没有执行；随后恢复原代码。增加除法时，再由零除案例引出相应错误处理。

## 3. 本章结束时的文件

```text
my-agent/
  pyproject.toml
  model.py       # 第 00 章手写的 HTTP 客户端
  tools.py       # 本章手写的注册和执行
  agent.py       # 从第 01 章循环改出的多工具版本
  demo.py
```

`from tools import execute_call` 指向这个目录中的 `tools.py`，不需要安装教程框架，也不需要导入仓库里的 `lessons`。
