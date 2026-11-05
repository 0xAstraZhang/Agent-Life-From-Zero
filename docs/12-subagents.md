# 第 12 章：把一次独立调查交给子 Agent

[上一章：Hooks 与规划](11-hooks-planning.md) · [目录](README.md) · [下一章：Skills](13-skills.md)

主 Agent 想知道项目怎样运行测试。我们给它一个 `Delegate` 工具；工具里面启动另一个 Agent，让它读取项目记录，再把结论交回来。先完成这次具体调查，再提取工厂和共享预算。

## 1. 先看清父子之间传递什么

```text
父 Agent → Delegate(task)
              ↓
          子 Agent：新的 history
              ↓
          ReadNote → project.txt 的内容
              ↓
          子 Agent 的最终回答
              ↓
父 Agent ← ToolResult(summary)
```

主循环无需增加消息类型。子 Agent 只是普通工具内部的一段程序；对父 Agent 来说，调用和结果仍然是一对 `ToolCall`、`ToolResult`。

## 2. 最小委派工具

先准备一个只会读固定 `project.txt` 的工具，把它交给子执行器。父执行器只有 `Delegate`，子执行器只有 `ReadNote`。这样第一次实验就能看清各自的工具和历史。

下面是 [lessons/ch12.py](../lessons/ch12.py) 中的完整委派函数：

```python
def delegate_tool(model, runner):
    def execute(args, context):
        child = Agent(model, runner, "先读取项目记录，再给出结论和证据。", max_turns=4)
        final = None
        for event in child.run(args["task"]):
            if event["type"] == "result":
                final = event
        if final is None:
            return ToolResult("", "子 Agent 没有返回终止结果", True)
        return ToolResult("", final["text"], final["status"] != "success")

    spec = ToolSpec("Delegate", "委派一个读取项目记录的独立调查。", {
        "type": "object", "properties": {"task": {"type": "string"}},
        "required": ["task"], "additionalProperties": False,
    })
    return Tool(spec, execute, read_only=True)
```

函数需要的 `Agent`、`Tool`、`ToolSpec`、`ToolResult` 已在前章出现。`execute()` 每次创建一个新的 Agent，所以它从 system 和本次 task 开始。循环取出子 Agent 的最终结果，再把文本交给父 Agent。

子任务未成功结束时返回错误结果；父 Agent 可以据此决定下一步。调用 ID 仍由外层 `ToolRunner` 填回，不需要子 Agent 猜测父调用的 ID。

## 3. 运行一次真实调查

沿用第 00 章的模型环境变量。可在教程仓库根目录先导出独立阶段：

```bash
uv run python scripts/check_tutorial.py --chapter 12 --export /tmp/minicode-ch12
cd /tmp/minicode-ch12
uv sync
```

在仓库根目录或导出目录运行：

```bash
uv run python examples/12_subagents.py --step minimal
```

[示例](../examples/12_subagents.py) 创建以下真实文件，再使用真实 API 运行父子两个 Agent：

```text
项目名称：星河
测试命令：python -m pytest
```

观察顺序：父 Agent 提出委派 → 子 Agent 实际读取 `project.txt` → 父 Agent 收到调查结果 → 父 Agent 回答。回答措辞可以变化，证据应来自文件。

修改文件里的测试命令，再运行一次。子 Agent 的初始历史不包含父历史，因此委派任务应写清“读什么、调查什么、返回什么”；不能只说“继续那个问题”。

## 4. 再把固定依赖换成工厂

最小实现传入现成的模型和执行器。需要为不同子任务创建依赖时，再使用 [subagent_tool()](../lessons/stages/12/minicode/subagents.py)：

```python
delegate = subagent_tool(
    model_factory=lambda: child_model,
    runner_factory=lambda context: child_runner,
    max_turns=4,
)
```

工厂使不同调查可以选择不同模型、工作区或工具集合。增强实现继续限制子执行器只读，并排除 `Delegate` 自己，防止递归委派。

这时再考虑审批对象与 hooks：子执行器复用工作区和审批配置；若增加可改写调用的 hook，让改写后的调用仍经过只读 `PermissionRunner`。只读不等于适合并行，本章保持串行执行。

## 5. 再让父子共享一个预算

先看到完整往返，再限制总请求次数：

```python
budget = RequestBudget(8)
parent_model = BudgetModel(raw_parent_model, budget)
delegate = subagent_tool(
    lambda: child_model,
    lambda context: child_runner,
    max_turns=4,
    budget=budget,
)
```

每次调用模型前从同一个 `budget` 领取一次额度，父子请求加在一起。`enhanced` 运行工厂和预算版本：

```bash
uv run python examples/12_subagents.py --step enhanced
```

离线检查点固定经历四次请求：父委派、子读文件、子解释、父汇总。真实模型可能需要不同的轮数，所以检查实际记录，不断言固定轮数。请求预算也不等于金额预算，第 15 章再处理 token 与费用指标。

增强实现还限制摘要长度，保留失败状态。父 Agent 收到的是子任务的结论，不会自动把所有子历史复制回来。

## 检查与练习

```bash
uv run python examples/12_subagents.py --step minimal --offline
uv run python examples/12_subagents.py --step enhanced --offline
uv run python -m pytest tests/test_lesson_12.py tests/test_subagents.py -q
```

先检查子 Agent 确实读过文件、父 Agent 收到的是工具结果，再验证独立历史、禁止写操作、禁止嵌套和共享预算。

练习：再给子 Agent 一个固定的配置文件读取工具，让它比较两个文件。两个调查能独立形成结论时适合委派；需要交替修改同一状态时，先留在一个 Agent 中完成。
