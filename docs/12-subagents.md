# 第 12 章：子 Agent，委派独立的只读调查

上一章：[Hooks 与规划](11-hooks-planning.md) · 下一章：[Skills](13-skills.md)

一个 Agent 可以连续调查所有问题，但它的历史也会装进所有调查细节。
当任务可以拆成独立的问题时，可以让子 Agent 分别调查，只把结论交回主 Agent。

本章新增 [subagents.py](../minicode/subagents.py)。
先实现只读、串行、不允许嵌套的委派。
上下文隔离、工具约束和共同预算清楚之后，再考虑更复杂的调度。

`subagents.py` 是本章的完整新增文件。示例只展示父子请求顺序；接入累积应用时沿用上一章的 `tools`、`permission_options`、`assemble()`，不要重置已有权限和 hooks。

## 本章的完整累积目录

导出只包含第 00–12 章文件的工作目录，再在里面运行示例和本章测试：

```bash
uv run python scripts/check_tutorial.py --chapter 12 --export /tmp/minicode-ch12
cd /tmp/minicode-ch12
uv sync
uv run python -m pytest tests -q
```

目标必须是新目录或空目录。导出目录的 README 列出本章新增、替换的完整文件和实验命令。这些文件可直接编辑；隔离验收会阻止它从原仓库加载尚未学习的模块。

## 本章验收

```bash
uv run python examples/12_subagents.py
```

输出：

```text
根据调查，下一步运行 pytest。
父子合计模型请求：3
```

这三次分别是：父 Agent 发起委派、子 Agent 回答、父 Agent 汇总。
它们共用一个 `RequestBudget`。

## 第一步：把子 Agent 当作工具

主循环已经能调用工具，不需要再新增一种“子 Agent 消息”。
提供一个 `Delegate` 工具，参数只有需要调查的任务。

```json
{"task":"找到当前项目的测试入口，列出文件位置和证据。"}
```

工具执行函数创建一个新的 Agent，给它一个新的消息列表，运行到终止事件，返回摘要。
主 Agent 最终收到的仍然是普通 `ToolResult`。

```text
父 Agent
  → Delegate(task)
      → 子 Agent 的独立 history
      → 只读工具
      → 终止结果
  ← ToolResult(summary)
```

三个模型协议都使用相同的工具往返。
适配器不需要知道 `Delegate` 内部又调用了一次模型。

## 第二步：用工厂创建依赖

`subagent_tool()` 不创建固定的服务商客户端。
应用传入两个工厂：模型工厂和工具执行层工厂。

下面是工厂的解释片段，`budget` 在第五步建立。
子执行器强制只读，继续使用父执行器的审批对象：

```python
def child_runner(context):
    return PermissionRunner(
        ToolRunner(tools, context),
        approval=permission_options["approval"],
        read_only=True,
    )


delegate = subagent_tool(
    model_factory=lambda: child_model,
    runner_factory=child_runner,
    max_turns=6,
    budget=budget,
)
```

模型工厂可以返回和父 Agent 相同的协议，也可以返回另一个适配器。
由于子 Agent 从新上下文开始，这里不需要跨服务商搬运已有的推理数据。

工具工厂接收父工具的 `ToolContext`，从而继续使用同一个工作区。
它应当返回已经配置好的只读 PermissionRunner，复用同一个审批对象。
不要在每个子 Agent 里创建不同的审批锁，否则多个确认输入可能同时出现。
父应用的 before hook 不直接搬进子执行器；否则 hook 可能在外层只读检查之后改写工具名。需要给子 Agent 加 hook 时，把 hook 包在 `read_only=True` 的 PermissionRunner 外面，让改写后的请求也经过硬性只读限制。

## 第三步：只暴露真正只读的工具

子 Agent 包住父执行层的只读视图，并排除 `Delegate` 自己：

```python
runner = readonly_runner(runner_factory(context), exclude={"Delegate"})
```

工具列表里没有 Write、Edit、Bash，也没有再次委派的入口。
运行时还会再次检查工具名，模型即使生成了这些调用也无法执行。

`Delegate` 可以标为 `read_only=True`，是因为本章实际限制了它的所有子操作。
如果将来允许子 Agent 写文件，就必须重新定义它的权限和并发属性。

这里的 `parallel_safe=False` 表示不默认把两个调查同时调度起来。
只读与适合并行是两个不同的属性，不能用一个布尔值替代。

## 第四步：从新的历史开始

子 Agent 的初始上下文只有自己的系统提示词与委派任务。

```python
child = Agent(
    model,
    runner,
    "你负责只读调查。给出结论、文件位置、证据和未验证事项。",
    max_turns=max_turns,
)
```

父 Agent 的完整 history 不会复制进去。
这能减少重复信息，也意味着委派任务必须自包含。
“继续查那个问题”通常不够；应写清目标、相关文件和需要返回的内容。

子 Agent 的 `result.status` 不是 `success` 时，Delegate 返回错误结果。
不能把用完轮数、模型截断或请求失败包装成正常调查结论。

## 第五步：共享一个请求预算

如果父子各有自己的预算，任务总开销可能超过用户预期。
本章使用容易验证的请求次数上限：每次真正调用模型前领取一个额度。

```python
from minicode.subagents import BudgetModel, RequestBudget, subagent_tool

budget = RequestBudget(limit=12)
parent_model = BudgetModel(raw_parent_model, budget)
delegate = subagent_tool(child_model_factory, child_runner, budget=budget)
tools.append(delegate)
runner = assemble(tools)
agent = Agent(parent_model, runner)
```

这段在启动任务前运行。`child_model_factory` 返回你已经配置好的子模型客户端；也可像离线示例一样返回一个新的 FakeModel。
追加 Delegate 后仍通过第 11 章的 `assemble()` 组装父执行器，所以已有工具、权限设置和 hooks 都保留。

父模型和每个子模型必须引用同一个 `budget` 对象。
`RequestBudget.take()` 在锁内检查并增加计数，因此即使以后引入并行，也不会同时领取最后一个额度。

流式请求也只领取一次额度，文字分片不会额外计数。
请求在网络层失败后已经消耗的额度不退回，以免不断失败的请求绕过上限。

这个预算计算模型请求次数，不等于美元费用。
不同模型的一次请求成本不同；真实费用还需要输入、输出、缓存用量及对应价格。
父 Agent 的 `usage` 只累计它直接获得的回复，子 Agent 的详细用量尚未合并进去。
[第 15 章](15-evals.md)会增加父子共享的 token 预算，并说明如何选择明确、可核对的观测指标。

## 第六步：只返回摘要

Delegate 返回子 Agent 的最终文本，最多 12,000 个字符。
不会把子 Agent 所有工具输出拼回父历史。

一个有用的调查结果包含：

- 结论。
- 对应的文件路径或数据来源。
- 支撑结论的证据。
- 尚未验证的部分。

父 Agent 仍然需要判断摘要是否足以支持下一步。
涉及修改时，父 Agent 应按现有文件工具规则重新读取将要改动的文件。

## 什么时候适合委派

适合：分别调查测试入口、配置加载路径、某个模块的调用关系。
它们可以独立完成，并各自返回一段明确的结果。

不适合：两个任务必须交替修改同一文件，或者子任务几乎需要完整父历史才能理解。
这时委派会增加沟通和请求次数，却没有带来清楚的隔离收益。

## 离线检查

```bash
uv run pytest tests/test_subagents.py -q
```

测试让子模型主动调用 Write，检查实际没有写操作；
同时检查子模型的工具列表没有 Delegate、初始历史只包含 system 和 user，
以及父子合计达到预算后下一次请求被拒绝。

完整示例见 [12_subagents.py](../examples/12_subagents.py)。

## 开源实现中的对应边界

[pi 的 Agent 类型](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/agent/src/types.ts)
把工具声明扩展成可执行的 Agent 工具。
本章沿着这个思路把委派实现为工具，保持模型接口和主循环不变。
更复杂的调度需要单独解决并发、取消和共享状态，不能仅靠“多启动几个 Agent”。

## 自检与练习

1. 让子 Agent 在两轮后耗尽 `max_turns`，检查父 Agent 收到的是错误结果。
2. 把共享预算设为 2，解释示例中的第三次请求为什么不能发出。
3. 为委派结果增加结构化的证据列表，再让父 Agent 检查至少一条证据。
