# 第 11 章：Hooks 与规划，让执行过程可观察、可约束

上一章：[会话与压缩](10-sessions-compaction.md) · 下一章：[子 Agent](12-subagents.md)

Agent 已经能执行工具、保存历史和压缩请求。
现在需要在执行前检查调用，在执行后记录结果，并把长任务的计划变成可检查的状态。

本章新增 [hooks.py](../minicode/hooks.py) 和 [planning.py](../minicode/planning.py)。
Hook 包装工具执行层，计划通过普通工具更新。
它们都不读取 Chat Completions、Responses 或 Anthropic 的原始字段。

## 先观察完成后的效果

```bash
uv run python examples/11_hooks_planning.py
```

输出：

```text
计划已记录。
[{'task': '定位问题', 'status': 'in_progress'}]
[('TodoWrite', False)]
```

最后一行来自执行后 hook，记录工具名称和是否出错。
它不是模型生成的内容。

## 第一步：确定 hook 的位置

工具执行已经被封装成 `runner.run(ToolCall) -> ToolResult`。
现在在外面再包一层：

```text
Agent
  → before hook
  → PermissionRunner
  → ToolRunner：参数校验、执行
  → after hook
  → ToolResult
```

`HookRunner` 仍然提供 `specs()` 和 `run()`，因此 Agent 无需增加新的参数。
模型看到的工具声明也不会因为存在 hook 而改变。

关键是顺序：执行前 hook 改写后的调用，必须重新经过权限和参数校验。
如果把 hook 放在权限检查后面，它可能把允许的 Read 改成未经允许的 Write。

## 第二步：实现执行前回调

`before` 接收 `ToolCall` 的副本，有三种返回方式：

| 返回值 | 行为 |
| --- | --- |
| `None` | 按原调用继续 |
| `ToolCall` | 使用新调用，再经过权限与校验 |
| `ToolResult` | 直接返回结果，不执行工具 |

改写时必须保留调用 ID。
否则模型发起的调用和应用返回的结果就失去对应关系。

例如，阻止写入某个教学用文件：

```python
from minicode.types import ToolResult

def before(call):
    if call.name == "Write" and call.arguments.get("path") == "protected.txt":
        return ToolResult(call.id, "这个文件由练习规则保护。", True)
    return None
```

上面只是 hook 形状示例；具体参数名以工具的 `ToolSpec` 为准。
真正的路径边界仍应由权限层和文件工具检查，不能只靠字符串匹配。

## 第三步：执行后只观察结果

`after` 收到调用和结果的副本，可以记录日志、统计耗时或收集评测数据。
这个版本不允许它把失败结果改成成功。

```python
records = []

def after(call, result):
    records.append({
        "tool": call.name,
        "call_id": call.id,
        "is_error": result.is_error,
    })
```

执行后的回调抛出异常时，返回内容会注明 hook 失败。
工具本身已经执行过，不能因此自动重试原来的写操作。

本章只运行由应用代码显式传入的 Python 回调。
不会扫描项目目录并执行其中的 shell 脚本。
将来支持配置化 hook 时，需要另外设计目录信任和授权流程。

## 第四步：组合现有执行层

```python
from minicode.hooks import HookRunner
from minicode.permissions import PermissionRunner

permission_runner = PermissionRunner(tool_runner, approval=approval)
runner = HookRunner(permission_runner, before=before, after=after)
agent = Agent(model, runner)
```

每一层只有一个职责。
HookRunner 调用内层 runner，不直接调用 `tool.execute()`，所以无法意外跳过内层规则。

## 第五步：把计划做成一个工具

计划由 `TodoList` 保存，模型通过 `TodoWrite` 更新。
每项有任务描述和三种状态：`pending`、`in_progress`、`done`。

```python
from minicode.planning import TodoList

todo = TodoList()
tools.append(todo.tool())
```

工具参数本身用 JSON Schema 校验；跨条目的约束在执行函数中检查：

```python
if sum(item["status"] == "in_progress" for item in args["items"]) > 1:
    return ToolResult("", "同一时间只能有一项 in_progress", True)
```

`ToolRunner` 会把这里暂时为空的 `call_id` 换成实际调用 ID。
工具实现不需要自己猜测当前调用来自哪一轮。

`TodoWrite` 修改内存状态，因此没有标为只读。
是否允许模型更新待办，由应用的权限配置决定。
示例直接使用本地 ToolRunner；接入交互应用时可明确允许这个工具。

## 第六步：计划模式必须落实到工具层

只在提示词里写“现在不要修改文件”不够。
`readonly_runner()` 提供一个只读视图：工具声明里只保留 `read_only=True` 的工具，
执行时再次拒绝不在这个集合中的调用。

```python
from minicode.planning import readonly_runner

plan_runner = readonly_runner(permission_runner)
planner = Agent(model, plan_runner, "只调查现状并提出计划。")
```

即使模型凭记忆调用 Write，运行时也不会执行它。
这个视图可以包住已有 PermissionRunner，继续使用同一套路径检查与审批对象。

当前计划模式只调查并输出计划，不包含 TodoWrite，因为更新待办也会修改状态。
需要让计划模式更新待办时，可以显式设计例外；不要把 TodoWrite 伪装成只读工具。

## 计划不是完成证据

模型把某项改成 `done`，只表示它声称完成了。
是否真正完成，要看工具结果和第 15 章的验收检查。
一个合理的计划可以帮助模型记住步骤，但不能替代测试、文件检查或用户要求。

## 离线检查

```bash
uv run pytest tests/test_extensions.py -k 'hook or todo' -q
```

测试会把 Read 改写成 Write，确认仍然被权限层拦截；
还会检查 ID 不可改写、after 不能掩盖失败、多个进行中任务被拒绝，以及计划模式不能写文件。

完整示例见 [11_hooks_planning.py](../examples/11_hooks_planning.py)。

## 开源实现中的对应边界

[pi 的 Agent 循环](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/agent/src/agent-loop.ts)
把工具参数验证、执行过程和结果消息组织放在 Agent 层。
minicode 把这些步骤拆成小的 runner，便于逐章看清新增行为。
协议适配器始终不负责执行 hook 或决定权限。

## 自检与练习

1. 写一个 after hook，记录每个工具的成功和失败次数。
2. 写一个 before hook，改写合法参数，再证明改写后仍会执行参数校验。
3. 给计划增加“完成依据”字段，但不要让这个字段本身成为任务完成的判定标准。
