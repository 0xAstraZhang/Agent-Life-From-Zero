# 第 15 章：评测、用量与失败分类

上一章：[MCP](14-mcp.md) · [目录](README.md) · 下一章：[交付](16-shipping.md)

## 这一章的结果

到这里，模型可以通过三个协议使用同一套工具，还能保存会话和委派调查。现在需要回答：任务到底做成了吗？失败发生在协议、执行器，还是模型选择？下一次请求是否会超过可接受的用量？

本章新增 [evals.py](../minicode/evals.py)、[budget.py](../minicode/budget.py)，实验是 [15_evals.py](../examples/15_evals.py)，验收见 [test_app.py](../tests/test_app.py)。

## 第一步：把完成条件写成可检查的结果

模型说“已经修改”不等于文件真的修改。一次修改任务至少检查三件事：

1. 终止事件的 `status` 是否符合预期。
2. 工具是否返回错误，错误是否符合该任务的预期。
3. 文件内容或程序行为是否真的达到目标。

实验准备一个临时目录，写入 `answer = 41`，然后用固定剧本调用 Read、Edit，最后检查文件是不是 `answer = 42`。

```python
model = FakeModel([
    call("Read", path="answer.py"),
    call("Edit", path="answer.py", old="41", new="42"),
    "完成",
])
```

这里允许 Edit，但不允许 Write 或 Bash。这样的权限设置使测试只验证需要的操作。实验结束后临时目录自动清理。

另两个任务分别检查拒绝写入和未知工具。它们的成功标准包含“错误被回传”和“文件没有产生”，而不是要求每次工具都成功。

```bash
uv run python examples/15_evals.py
```

预期报告含三项：

```json
[
  {"task": "read_then_edit", "passed": true, "tool_errors": 0},
  {"task": "deny_write", "passed": true, "tool_errors": 1},
  {"task": "unknown_tool", "passed": true, "tool_errors": 1}
]
```

这三项验证 harness 的执行链。FakeModel 没有推理能力，所以这些结果不能当成真实模型的任务成功率。

## 第二步：把测试分成三层

| 层次 | 输入 | 验证什么 |
| --- | --- | --- |
| 内核测试 | FakeModel | 循环出口、错误回传、权限、中断恢复 |
| 协议契约测试 | MockTransport 与 SSE 事件 | 三种请求格式、响应转换、调用 ID、原始续轮数据 |
| 任务评测 | 真实模型与隔离工作目录 | 能否读懂任务、选择工具、修改并验证 |

同一层测试不能代替另一层。协议 fixture 全部通过，仍然需要一次真实服务检查；真实模型碰巧做对一次，也不能证明流断开时不会错误执行工具。

```bash
uv run python -m pytest -q
uv run python scripts/check_tutorial.py
```

第二条还会运行每章实验和核对本地 Markdown 链接，确保文档中的入口没有悄悄断开。

## 第三步：在模型边界累计用量

第 12 章按请求次数限制委派。这里进一步按服务商返回的 token 用量累计，预算对象与模型实例分开，可以给父子 Agent 共用。

```python
from minicode.budget import Budget, BudgetModel

budget = Budget(token_limit=50_000)
model = BudgetModel(base_model, budget)
```

`BudgetModel.complete()` 的顺序是：检查累计用量 → 请求模型 → 累计本次用量 → 返回原来的 `ModelReply`。

流式模式在 `done` 事件上累计一次，不按 delta 重复计数。压缩模型也放在预算包装器外面，它产生的摘要请求会经过同一个预算边界。

```text
CompactingModel → BudgetModel → 协议适配器
```

这个预算是**下一次请求前的软上限**：当前请求结束时可能超限；断流且没有 usage 时也无法得知完整账单。它不承诺硬性美元封顶。若要换算费用，需要显式配置对应模型、缓存、输入输出的价格，并记录核对日期，不能给所有模型写同一个默认价格。

CLI 使用 `--token-budget`，`--max-output-tokens` 控制单次请求的输出上限，`--max-turns` 控制当前任务的循环轮数。

JSON 终止事件的 `usage` 是当前 Agent 观测到的用量，`budget_usage` 是本次进程中父子 Agent 共用预算的累计用量。恢复会话不会把上一次进程的用量当成这次运行的预算。

## 第四步：设计一组真实任务

在线评测前，在独立副本或容器里创建小任务，每项都写清楚初始状态、允许工具、验收函数和上限。例如：

| 任务 | 验收 |
| --- | --- |
| 定位一个函数的调用位置 | 返回的路径与行号存在，说明与代码相符 |
| 修复一个边界条件 | 原有测试和新增的失败用例通过 |
| 修改配置中的一个值 | 指定字段变化，其他字段保持不变 |
| 操作被拒绝 | 未产生对应副作用，报告拒绝原因 |
| 文件被外部修改 | 旧内容校验阻止覆盖，模型重新读取 |

对每个协议使用支持函数工具的实际模型。记录日期、模型 ID、base URL、任务版本和运行参数；每项重复几次，将波动单独报告。

先运行一个只读 smoke test，再决定是否开启写工具：

```bash
uv run minicode --read-only --max-turns 5 --token-budget 10000 -p "阅读 README，列出三个支持的协议"
```

这会请求真实服务，需要先设置第 03–05 章的环境变量。

## 第五步：按失败来源统计

- `length`：输出被截断；不能计为完成，也不能执行残缺的工具调用。
- `refusal`：模型拒绝，不应当作网络故障重试。
- `error`：查看是认证、HTTP、协议解析还是执行器失败。
- `max_turns`：工具循环没有在限制内结束。
- `cancelled`：用户中断，未完成调用的结果被记录为未知。
- `success` 但验收不通过：模型结束了任务，但目标没有达成。

保存事件时注意工具结果可能含项目内容。会话目录被 `.gitignore` 排除；向别人分享评测记录前，按任务需要删去密钥和私人数据。

## 验收与练习

```bash
uv run python -m pytest tests/test_app.py -q
uv run python -m minicode.evals
```

- 把 Edit 的旧字符串改错，确认任务验收失败，而不是被最后一句“完成”骗过。
- 添加一个模型连续调用不存在工具直到轮数耗尽的任务。
- 把 token 预算设得很小，确认下一次模型请求没有发送。
- 用三组 HTTP fixture 运行同一任务，检查只有适配器编码不同。

下一章把这些组件和检查打包成用户可以安装的 CLI。
