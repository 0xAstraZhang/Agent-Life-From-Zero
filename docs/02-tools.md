# 第 2 章：把工具从循环中拆出来

先回到教程仓库根目录，执行下面的导出命令；随后进入独立的本章目录。手写时保留自己的练习目录，用导出版本核对文件清单和差异：

```bash
uv run python scripts/check_tutorial.py --chapter 02 --export /tmp/minicode-ch02
cd /tmp/minicode-ch02
uv sync
```

以下运行和测试命令在本章练习目录中执行。目录只含第 00–02 章的代码与测试；文件增量由[阶段清单](../lessons/stages.json)记录。导出目标必须为空，不会覆盖已有练习。

上一章：[最小 Agent Loop](01-agent-loop.md) · 下一章：[Chat Completions 适配](03-chat-completions.md)

第一章只有一个工具分支。这一章保留它的消息和假模型，将工具说明、注册、参数校验与执行拆出来。
完成后增加新工具只需注册一个对象，不必修改 Agent 循环。

本章完整快照是 [`lessons/ch02.py`](../lessons/ch02.py)，它只复用第一章的类型与假模型，不导入最终的 `minicode.agent`。
这份 Agent 没有流式分支、会话、压缩、权限或 hooks；它们在后续需要时再加入。

## 1. 保留前一版，建立新文件

如果上一章是在独立练习目录手写的，将那份 `agent.py` 保存为 `lessons/ch01.py`，再增加空的 `__init__.py` 和本章文件：

```text
lessons/
  __init__.py       # 空文件，让 Python 把目录当作包
  ch01.py          # 第一章完整代码，保持可运行
  ch02.py          # 这一章的新代码
```

仓库已包含这些文件，可以直接对照。下面的代码块依次写进 `ch02.py`，先导入：

```python
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from .ch01 import FakeModel, Message, ToolCall, ToolResult, ToolSpec, call, message_text, tool_calls


```

`from .ch01 import ...` 明确表示本章复用已经学过的定义。运行时使用 `python -m lessons.ch02`，让 Python 按包解析相对导入。
`jsonschema` 是本章的新依赖，仓库的 `uv sync --locked` 已经安装；独立练习项目可运行 `uv add jsonschema`。

对照前一版，只做三处变化：

| 第一章 | 本章替换或新增 |
| --- | --- |
| `run()` 中硬编码 `read_file` 分支 | `ToolRunner.run()` 根据名称查注册表 |
| 局部 `messages` | `Agent.history`，可以连续接收多条用户输入 |
| 直接调用工具函数 | 先校验参数，再执行，并归一结果 |

`Message`、`ToolCall`、`ToolResult`、`ToolSpec` 和 `FakeModel.complete()` 的含义都不变。

## 2. 写工具定义与执行器

给模型的声明仍然是第一章的 `ToolSpec(name, description, parameters)`。
Python 执行器另外需要回调和工作目录，因此增加下面这些代码：

```python
@dataclass
class ToolContext:
    cwd: Path


@dataclass
class Tool:
    spec: ToolSpec
    execute: Callable[[dict, ToolContext], str | ToolResult]
    read_only: bool = False
    parallel_safe: bool = False


class ToolRunner:
    def __init__(self, tools, context):
        self.context = context
        self.tools = {tool.spec.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("工具名称不能重复")
        for tool in tools:
            Draft202012Validator.check_schema(tool.spec.parameters)

    def specs(self):
        return [tool.spec for tool in self.tools.values()]

    def run(self, request):
        tool = self.tools.get(request.name)
        if tool is None:
            return ToolResult(request.id, f"未知工具：{request.name}", True)
        if not isinstance(request.arguments, dict):
            return ToolResult(request.id, "参数必须是对象", True)
        try:
            Draft202012Validator(tool.spec.parameters).validate(request.arguments)
        except ValidationError as error:
            return ToolResult(request.id, f"参数校验失败：{error.message}", True)
        try:
            output = tool.execute(request.arguments, self.context)
            if isinstance(output, ToolResult):
                return ToolResult(request.id, output.content, output.is_error)
            return ToolResult(request.id, str(output))
        except Exception as error:
            return ToolResult(request.id, f"{type(error).__name__}: {error}", True)


```

按职责阅读这个实现：

- `ToolContext.cwd` 是运行上下文。模型可以指定相对文件路径，不能改写工具执行器的工作目录。
- `Tool` 将声明和 Python 函数登记在一起。
- `specs()` 只导出声明，不把 Python 函数交给模型。
- `run()` 统一处理查找、校验、执行和错误结果。

工具说明没有 `{"type":"function","function":...}` 这样的外部包装。第三至五章分别由三个适配器产生所需格式。

JSON Schema 校验必须在执行之前。`{"a":"2","b":3}` 里的字符串不会自动当作数字；嵌套字段、数组和额外参数也由校验器检查。
创建执行器时先验证 schema 和重复名称，避免到运行中才发现工具注册本身有问题。

## 3. 分开表达只读与并行能力

两个工具属性回答不同的问题：

| 属性 | 含义 |
| --- | --- |
| `read_only` | 是否修改工具作用范围内的状态 |
| `parallel_safe` | 多次调用同时执行，是否可能发生共享状态冲突 |

例如，读取一个有状态游标的工具可能不修改文件，但未必能安全并行。
一个会启动可写子 Agent 的工具也不能声明自己只读。

这一章仍然全部串行执行。先记录正确的工具事实，第七章权限包装器使用 `read_only`；调度器需要并行时再检查独立属性。

回调可以返回字符串，也可以返回 `ToolResult("", content, is_error=True)`。
回调不知道本次调用 ID，留空即可：执行器用实际 `request.id` 重建结果。
未知工具、参数错误和回调异常都变成工具结果，让模型决定下一步。

## 4. 写没有未来功能的 Agent

第一章的循环主体仍然保留，只把工具分支替换成 `runner.run()`，并把历史搬进对象：

```python
class Agent:
    def __init__(self, model, runner, system_prompt="你是一个编程助手。", *, max_turns=20):
        if max_turns < 1:
            raise ValueError("max_turns 必须至少为 1")
        self.model, self.runner = model, runner
        self.max_turns = max_turns
        self.history = [Message.text("system", system_prompt)]

    def run(self, prompt):
        self.history.append(Message.text("user", prompt))
        for turn in range(1, self.max_turns + 1):
            try:
                reply = self.model.complete(self.history, self.runner.specs())
            except Exception as error:
                yield self._result("error", str(error), turn)
                return
            if reply.stop_reason not in {"stop", "tool_calls"}:
                yield self._result(reply.stop_reason, message_text(reply.message), turn)
                return
            calls = tool_calls(reply.message)
            if bool(calls) != (reply.stop_reason == "tool_calls"):
                yield self._result("error", "停止原因与调用不一致", turn)
                return
            prior_ids = {c.id for message in self.history for c in tool_calls(message)}
            ids = [c.id for c in calls]
            if any(not value for value in ids) or len(set(ids)) != len(ids) or prior_ids.intersection(ids):
                yield self._result("error", "工具调用 ID 缺失或重复", turn)
                return
            self.history.append(reply.message)
            if reply.stop_reason == "stop":
                yield self._result("success", message_text(reply.message), turn)
                return
            for request in calls:
                result = self.runner.run(request)
                self.history.append(Message("tool", [result]))
                yield {"type": "tool_result", "result": result, "call_id": result.call_id,
                       "content": result.content, "is_error": result.is_error}
        yield self._result("max_turns", "已达到最大轮数", self.max_turns)

    @staticmethod
    def _result(status, text, turns):
        return {"type": "result", "status": status, "text": text, "turns": turns}

```

现在循环的两条依赖很清楚：

```text
model.complete(history, runner.specs()) → ModelReply
runner.run(ToolCall)                  → ToolResult
```

它没有解析任何服务商的字段，也没有负责文件读写。
如果回复携带工具调用却标为 `stop`，循环返回错误；如果调用 ID 重复，也先停止，避免配错结果。
`length` 等非正常停止状态直接退出，不执行这条回复中的工具。

这个版本暂时不处理用户中断后的恢复。第八章才加入中断补记和流式消费，因此本章不能在 Ctrl+C 后保存半截历史并宣称可以直接续跑。

## 5. 加一个计算工具，观察模型修正参数

最后加入入口：

```python
def main():
    add = Tool(ToolSpec("add", "计算两个整数的和", {
        "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False,
    }), lambda args, ctx: str(args["a"] + args["b"]), read_only=True, parallel_safe=True)
    model = FakeModel([call("add", a="2", b=3), call("add", a=2, b=3), "2 + 3 = 5"])
    agent = Agent(model, ToolRunner([add], ToolContext(Path.cwd())))
    for event in agent.run("2 + 3 是多少？"):
        print({key: value for key, value in event.items() if key != "result"})


if __name__ == "__main__":
    main()
```

第一项剧本传入错误类型，第二项修正，第三项给出最终回答。`add` 回调只有一行；错误处理和循环不用为它再写一次。

运行：

```bash
uv run python -m lessons.ch02
uv run python examples/02_tools.py
```

两个命令执行同一份快照。预期先看到 `is_error=True` 的参数错误，再看到内容为 `5` 的工具结果，最后是 `status=success`、`turns=3`。

这次任务使用三次模型请求和两次工具调用，其中第一次调用没有通过参数校验，实际计算只执行一次。

## 6. 用同一组行为检查重构

```bash
uv run python -m pytest tests/test_ch01.py tests/test_ch02.py -q
```

保留第一章测试，再用第二章独立测试检查同样的行为：

- “读文件 → 工具结果 → 最终回答”仍然只需两轮。
- 未知工具错误进入下一轮模型请求。
- 截断回复中的调用不执行。
- 模型异常和轮数上限返回明确状态。

第一章快照保留在仓库中，测试也一直保留；不因第二章重构而删除旧版行为记录。
新的工具执行器还检查非法参数不能进入回调。

## 7. 从快照拆成后续包结构

至此已经亲手写过所有核心组件。进入协议适配前，把定义按职责拆到正式包：

| 已有定义 | 后续文件 |
| --- | --- |
| 第一章数据类与消息辅助函数 | `minicode/types.py` |
| 第一章 `FakeModel` 和 `call()` | `minicode/testing.py` |
| 本章 `ToolContext`、`Tool`、`ToolRunner` | `minicode/tool.py` |
| 本章 `Agent` | `minicode/agent.py` |

拆文件时把 `.ch01` 导入换成对应模块导入，循环算法不变。
仓库中的 `minicode/` 是完成所有章节后的参考版本，`lessons/` 保留当前学习阶段；不要把后续版本的整个 Agent 提前抄进第二章。

第三章因为要接收真实响应，才增加 `Usage`、调用参数解析错误，以及保存原始续轮数据的 `provider_data`。第八章再增加 `StreamEvent`、`stream()` 和中断补记。
每次扩充都对应一个已经出现的问题。

练习：增加除法工具，除数为零时返回错误结果。再注册两个同名工具，观察错误发生在启动时还是执行时。

工具声明与执行路由可以对照 [Codex 的 ToolRouter](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/core/src/tools/router.rs)阅读。本章只实现当前闭环需要的注册表。

下一章：[OpenAI Chat Completions](03-chat-completions.md)。
