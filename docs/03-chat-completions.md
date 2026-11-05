# 第 03 章：把已经写好的循环整理成 Agent

上一章：[工具注册](02-tools.md) · [目录](README.md) · 主线下一章：[编码工具](06-coding-tools.md)

你已经写出了真实请求、消息列表和工具分发。现在的问题是：`run()` 每次都新建历史，无法连续接收用户任务。本章把这份状态放进一个类，并让工具执行函数可以替换。循环算法仍然是前两章的算法。

本章结束后可以直接进入第 06 章。第 04、05 章是自己实现其他协议的选修，不影响主线。

## 1. 把历史移到对象上

继续使用自己的 `my-agent` 项目，保留 `model.py` 和 `tools.py`。将 `agent.py` 全部替换为下面的完整定义：

```python file=agent.py step=minimal
from tools import execute_call


class Agent:
    def __init__(self, model, tools, *, system="你是一个使用工具完成任务的助手。",
                 runner=None, max_turns=8, history=None):
        self.model = model
        self.tools = tools
        self.max_turns = max_turns
        self.history = list(history) if history is not None else [{"role": "system", "content": system}]
        self.runner = runner if runner is not None else lambda request: execute_call(request, tools)

    def run(self, prompt):
        self.history.append({"role": "user", "content": prompt})
        specs = [tool["spec"] for tool in self.tools.values()]
        for turn in range(self.max_turns):
            reply = self.model.complete(self.history, specs)
            message = reply["message"]
            calls = message.get("tool_calls") or []
            if reply["finish_reason"] == "stop" and not calls:
                self.history.append(message)
                return message.get("content") or ""
            if reply["finish_reason"] != "tool_calls" or not calls:
                raise RuntimeError("模型没有返回完整回答或完整调用")
            self.history.append(message)
            for request in calls:
                result = self.runner(request)
                self.history.append({"role": "tool", "tool_call_id": request["id"], "content": result})
        raise RuntimeError("已达到模型请求轮数上限")
```

`self.history` 保存此前对话，第二次调用 `run()` 时继续追加。传入 `history` 可以从一份已有记录开始，后面保存会话时会用到。

`runner` 表示本地怎样执行一次调用。默认使用第二章自己写的 `execute_call()`；下一步会用一个有实际用途的小函数替换它。类没有导入服务商 SDK，也没有执行 HTTP：它只调用已有 `model.complete()`。

把 `demo.py` 替换为：

```python file=demo.py step=minimal
from model import open_model
from agent import Agent
from tools import TOOLS

with open_model() as model:
    agent = Agent(model, TOOLS)
    print(agent.run("请调用 add 计算 2 加 3，再回答。"))
    for message in agent.history:
        print(message)
```

```bash
uv run python demo.py
```

输出先是回答，随后是完整历史。找到工具结果，并核对它的 `tool_call_id` 与前一条助手调用匹配。

## 2. 替换执行函数，观察一次调用

我们想在工具执行前后打印日志，但不想为此再复制整个循环。这个需求说明为什么构造函数接收 `runner`。

保留 `agent.py`，只将 `demo.py` 替换为：

```python file=demo.py step=enhanced
from model import open_model
from agent import Agent
from tools import TOOLS, execute_call


def log_and_execute(request):
    print("执行：", request["function"]["name"])
    result = execute_call(request, TOOLS)
    print("结果：", result)
    return result


with open_model() as model:
    agent = Agent(model, TOOLS, runner=log_and_execute)
    print(agent.run("请调用 add 计算 2 加 3，再回答。"))
    print(agent.run("刚才算出的结果是多少？这次直接回答即可。"))
```

运行 `uv run python demo.py`。先看到 `执行：add` 与 `结果：5`；第二个问题不重新计算，模型从同一个 Agent 的历史里读取刚才结果。

这一步全部依赖都已在正文写过：

| 本地模块 | 在哪里写成 | 当前职责 |
| --- | --- | --- |
| `model.py` | 第 00 章 | 配置、HTTP 请求、关闭客户端 |
| `tools.py` | 第 02 章 | 工具声明、参数校验、调用函数 |
| `agent.py` | 第 01–03 章 | 保持历史，决定继续调用还是返回 |
| `demo.py` | 每章 | 组装本次实验并观察结果 |

## 3. 从需要出发继续扩展

第 06 章把算术函数换成文件操作。第 07 章在 `runner` 中加入批准和拒绝；第 11 章再把刚才的日志函数提取为可组合的回调。这些扩展使用同一个循环。

如需其他模型协议，可以先做 [Responses](04-responses.md) 或 [Anthropic Messages](05-anthropic.md)：给新客户端提供相同的 `complete(messages, tools)`，内部转换请求和回复。你的 Agent 就可以继续使用，而不需要复制循环。

## 4. 验收与参考实现

确认你能不看仓库代码解释：消息在哪里保存、哪个函数发 HTTP、哪个函数真正执行工具、第二次用户输入怎样复用历史。

仓库的 [完整 Agent](../minicode/agent.py) 还实现了更完整的状态、流式、取消和计量。这是进阶对照，主线手写程序不导入它。正文中的客户端仍使用 Chat Completions 字典格式；直到需要另一种协议时，再写转换层。
