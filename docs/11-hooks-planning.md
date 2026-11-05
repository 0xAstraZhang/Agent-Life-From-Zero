# 第 11 章：先记录工具执行，再让模型维护计划

[上一章：会话与压缩](10-sessions-compaction.md) · [目录](README.md) · [下一章：子 Agent](12-subagents.md)

本章有两个小实验：在工具执行后记一条日志；把待办列表交给模型通过工具更新。两者都复用已有循环，分别跑通即可。

## 1. 最小 hook：在执行之后调用一个函数

已有执行器提供 `run(call) → result`。在它外面包一层，拿到结果后调用 `after`，再把同一个结果返回 Agent：

```python
class LoggingRunner:
    def __init__(self, inner, after):
        self.inner, self.after = inner, after
        self.tools, self.context = inner.tools, inner.context

    def specs(self):
        return self.inner.specs()

    def run(self, call):
        result = self.inner.run(call)
        self.after(call, result)
        return result
```

这就是 hook 的骨架。`specs()` 保持原来的工具声明，`run()` 增加一次观察。下面给它传一个记录函数：

```python
records = []

def after(call, result):
    records.append((call.name, result.is_error))

runner = LoggingRunner(permission_runner, after)
```

`minimal` 检查点让真实模型调用 `ReadNote` 读取临时项目记录。回答后打印 `records`，可以看到日志是 Python 在执行工具之后写的。

## 运行本章检查点

沿用第 00 章配置的 `MINICODE_MODEL`、`MINICODE_BASE_URL`、`MINICODE_API_KEY`。默认使用真实 API；回答措辞不固定，观察消息与工具的实际变化。需要独立工作目录时，在教程仓库根目录导出：

```bash
uv run python scripts/check_tutorial.py --chapter 11 --export /tmp/minicode-ch11
cd /tmp/minicode-ch11
uv sync
```

在仓库根目录或导出目录运行：

```bash
uv run python examples/11_hooks_planning.py --step minimal
uv run python examples/11_hooks_planning.py --step enhanced
uv run python examples/11_hooks_planning.py --step todo
uv run python examples/11_hooks_planning.py --step todo-enhanced
```

给同一命令加 `--offline` 可运行固定回复的回归检查。它用来验证程序控制流；学习时先观察真实请求。协议可用 `--api` 选择。


## 2. hook 增强：再增加改写、拦截和失败处理

把原型换成 [HookRunner](../minicode/hooks.py)，`enhanced` 先复现相同的日志行为。然后可以给它传 `before`：

```python
def before(call):
    if call.name == "Write" and call.arguments.get("path") == "protected.txt":
        return ToolResult(call.id, "练习规则不允许写这个文件。", True)
    return None

runner = HookRunner(permission_runner, before=before, after=after)
```

`before` 返回 `None` 就继续；返回 `ToolResult` 就结束这次调用；返回 `ToolCall` 就改用新调用。引入改写时，要同步保留调用 ID，并让改写后的调用重新经过权限与参数校验：

```text
Agent → before → PermissionRunner → ToolRunner → after → Agent
```

增强实现给回调传副本，捕获回调异常；执行后回调不能改写已经发生的结果。工具已执行时，日志回调出错不能成为自动重做写操作的理由。

## 3. 最小计划：一个列表和一个更新工具

先创建 `items = []`。模型调用 `TodoWrite` 时，用传入的清单替换列表，再把新清单作为工具结果返回。

```python
def todo_tool(items):
    def execute(args, context):
        items[:] = args["items"]
        return json.dumps(items, ensure_ascii=False)

    spec = ToolSpec("TodoWrite", "用完整任务清单更新计划。", {
        "type": "object", "properties": {"items": {"type": "array", "items": {
            "type": "object", "properties": {
                "task": {"type": "string"},
                "status": {"type": "string", "enum": ["pending", "in_progress", "done"]}},
            "required": ["task", "status"], "additionalProperties": False}}},
        "required": ["items"], "additionalProperties": False,
    })
    return Tool(spec, execute)
```

完整导入见 [lessons/ch11.py](../lessons/ch11.py)。核心是 `items[:] = args["items"]`：保留同一个列表对象，更新它的内容。声明告诉模型每一项需要任务文字和状态。

`todo` 检查点独立运行这条路径。它让模型先写入两步计划，再打印实际保存的列表。没有 hook 也能运行，因为计划本身就是普通工具。

## 4. 计划增强：从状态更新到明确约束

换用 [TodoList](../minicode/planning.py)，再加入“最多一项进行中”的跨条目检查。`todo-enhanced` 使用这个版本。

```python
todo = TodoList()
tools.append(todo.tool())
permission_options["allowed"] = (*permission_options["allowed"], "TodoWrite")
runner = assemble(tools)
```

`TodoWrite` 会修改内存状态，应用显式授权它。计划中的 `done` 只是模型更新的状态，实际完成仍要看文件和测试结果。

若要增加只调查、不修改文件的计划模式，再用 `readonly_runner()` 过滤并拒绝写工具。它不包含修改待办状态的 `TodoWrite`；这与“维护计划列表”是两个不同功能。

## 5. 接回已有应用

保留之前的工具对象、上下文、审批配置和回调。在启动下一次任务前重建外层包装：

```python
def assemble(current_tools):
    inner = ToolRunner(current_tools, context)
    permissions = PermissionRunner(inner, **permission_options)
    return HookRunner(permissions, **hook_options)
```

其中 `permission_options` 保存原来的 `approval`、`allowed`、`read_only`；`hook_options` 保存本章的 `before`、`after`。新增工具时扩充内层列表，已有会话沿用原来的 Agent 和历史。

## 检查与练习

```bash
uv run python examples/11_hooks_planning.py --step minimal --offline
uv run python examples/11_hooks_planning.py --step enhanced --offline
uv run python examples/11_hooks_planning.py --step todo --offline
uv run python examples/11_hooks_planning.py --step todo-enhanced --offline
uv run python -m pytest tests/test_lesson_11.py tests/test_hooks.py tests/test_planning.py -q
```

先确认日志发生在执行之后、计划更新能进入下一轮请求，再检查改写后的权限、ID 保持、回调异常和计划状态约束。

练习：让模型更新计划中的第二项，观察列表变化；再给 after 加入耗时记录。运行入口见 [11_hooks_planning.py](../examples/11_hooks_planning.py)。
