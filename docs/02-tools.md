# 第 02 章：增加第二个工具，循环不用跟着改

上一章：[最小 Agent Loop](01-agent-loop.md) · [目录](README.md) · 下一章：[提取协议适配器](03-chat-completions.md)

这一章完成一个连续任务：模型先调用 `add(2, 3)`，再把结果交给 `multiply(5, 4)`。先做名称到函数的映射，再集中处理参数错误。

## 1. 准备本章目录

沿用已有 API 配置。完整文件是 [lessons/ch02.py](../lessons/ch02.py)，保留第 00 章的客户端和第 01 章的消息格式。

在仓库根目录可导出独立版本：

```bash
uv run python scripts/check_tutorial.py --chapter 2 --export /tmp/minicode-ch02
cd /tmp/minicode-ch02
uv sync
```

导出目标须为空。以下命令在仓库根目录或导出的练习目录执行。

## 2. 最小原型：注册两个动作

模型需要工具声明，Python 需要可以执行的函数。把它们放在同一条记录里：

```python
from lessons.ch01 import ADD

MULTIPLY = {"type": "function", "function": {
    "name": "multiply", "description": "计算两个整数的积",
    "parameters": ADD["function"]["parameters"],
}}
TOOLS = {
    "add": {"spec": ADD, "execute": lambda a, b: a + b},
    "multiply": {"spec": MULTIPLY, "execute": lambda a, b: a * b},
}
```

给模型的只有 `spec`。`execute` 始终留在本地：

```python
import json


def execute(request, tools):
    function = request["function"]
    tool = tools[function["name"]]
    arguments = json.loads(function["arguments"])
    return str(tool["execute"](**arguments))
```

这样第一章循环只需替换两处：

| 第一章 | 这一章 |
| --- | --- |
| 传递 `[ADD]` | 传递 `[tool["spec"] for tool in tools.values()]` |
| 调用 `add(request)` | 调用 `execute(request, tools)` |

历史追加、调用 ID 配对、再次请求模型的顺序完全相同。这里还不需要新增 `Agent` 类或消息数据类。

运行这个检查点：

```python
# 可运行检查点：02-minimal
from lessons.ch00 import open_chat
from lessons.ch02 import run

with open_chat() as model:
    answer, history = run(model, "先调用 add 计算 2 加 3，再用 multiply 把结果乘以 4。")
    for message in history:
        if message["role"] == "tool":
            print(message["tool_call_id"], message["content"])
    print(answer)
```

对应的完整入口：

```bash
uv run python examples/02_tools.py --step minimal
```

预期先看到加法结果 `5`，再看到乘法结果 `20`，最后是模型的回答。第三次请求必须同时包含前两个工具的调用和结果。

到这里先加一个 `subtract` 工具，确认只改注册表就能让模型使用它。这是本章第一个验收点。

## 3. 增强一步：先检查参数，再执行

两个工具都需要整数 `a`、`b`，继续手写检查会产生重复。现在才把工具声明中的 JSON Schema 交给校验器：

```python
arguments = json.loads(function["arguments"])
Draft202012Validator(tool["spec"]["function"]["parameters"]).validate(arguments)
result = tool["execute"](**arguments)
```

顺序很重要：解析、校验都通过，才执行函数。字符串 `"2"` 不会自动变成整数；缺少字段和多余字段也会被发现。

完整文件提供 `execute_checked()`：未知工具、JSON 错误、参数校验失败及当前算术回调的类型/值错误都会变成 `工具错误：…`。循环仍会为它追加带原调用 ID 的结果，让模型看到失败原因。

```python
# 可运行检查点：02-enhanced
from lessons.ch00 import open_chat
from lessons.ch02 import run

with open_chat() as model:
    answer, history = run(model, "先用 add 算 2 加 3，再用 multiply 把结果乘以 4。", enhanced=True)
    print(answer)
```

```bash
uv run python examples/02_tools.py --step enhanced
```

真实模型可能一次就给对参数。稳定观察修正过程时再运行：

```bash
uv run python examples/02_tools.py --offline --step enhanced
```

离线夹具先提供错误的 `add(a="2", b=3)`，随后修正为整数，再调用乘法。观察错误结果和正常结果都经过同一条回传路径。

## 4. 哪些细节现在还不需要

当前注册表已能说明“声明给模型、函数留在本地、按名称分发”的逻辑。工作目录上下文在读文件时再加入；只读属性在权限章讲；并发能力在需要并发调度时再讲。

第三章会把注册表整理成 `Tool` 和 `ToolRunner`，把字典消息整理成公共类型。提取这些结构的目的是让 Agent 循环不再依赖某一种 HTTP 字段，并非增加一种新的循环算法。

## 5. 验收与练习

```bash
uv run python -m pytest tests/test_ch01.py tests/test_ch02.py -q
```

测试检查两工具的完整历史、结果 ID 配对、非法参数不会进入函数，以及注册一个新工具不需要改循环。

练习：增加除法，并决定除数为零时如何返回可读的错误；先让有效输入算出结果，再补零除错误。不要先为尚不存在的工具搭建一套通用异常体系。
