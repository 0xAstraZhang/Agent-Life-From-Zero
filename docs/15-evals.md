# 第 15 章：让程序检查 Agent 是否做成了任务

上一章：[MCP](14-mcp.md) · 下一章：[交付](16-shipping.md)

本章先只评测一个真实任务：文件里是 `answer = 41`，让 Agent 改成 `answer = 42`，由独立代码检查文件。先把“任务 → 运行 → 验收”做通，再增加预算、任务集合和失败分类。

```bash
uv run python scripts/check_tutorial.py --chapter 15 --export /tmp/minicode-ch15
cd /tmp/minicode-ch15
uv sync
uv run python examples/15_evals.py --step minimal
```

真实 API 配置沿用[第 00 章](00-setup.md)。本章直接使用 Python 入口，不依赖下一章才创建的 CLI。完整文件是 [lessons/ch15.py](../lessons/ch15.py)。

## 1. 先写验收条件

实验在临时目录里创建一份文件：

```python
target = root / "answer.py"
target.write_text("answer = 41\n", encoding="utf-8")
```

目标是文件内容准确变成下面这样：

```python
file_passed = target.read_text(encoding="utf-8") == "answer = 42\n"
```

这个判断在 Agent 运行结束后执行。它不看模型是否说了“完成”，而是直接读取实际产物。

## 2. 让真实 Agent 尝试任务

沿用前面的工具和权限，只允许本次需要的 Edit：

```python
runner = PermissionRunner(
    ToolRunner(build_coding_tools(include_bash=False), ToolContext(root)), allowed=["Edit"])
events = list(Agent(model, runner, max_turns=6).run(
    "先读取 answer.py，再用 Edit 将 answer 的值从 41 改成 42，其他内容保持原样。"))
final = events[-1]
file_passed = target.read_text(encoding="utf-8") == "answer = 42\n"
report = {
    "status": final["status"],
    "file_passed": file_passed,
    "passed": final["status"] == "success" and file_passed,
}
```

`status` 回答“Agent 是否正常结束”，`file_passed` 回答“任务是否做成”。两个条件都满足才算通过。工具中途出错、随后正确重试并完成任务，不必自动判整项失败。

运行最小检查点后应看到包含 `passed` 的 JSON 报告。真实模型可能失败；先看最后的文件与事件，再决定原因。修改任务目标和验收条件，重新做一次。

## 3. 增强检查点：给同一个任务加用量预算

```bash
uv run python examples/15_evals.py --step enhanced
```

只把模型换成预算包装器，保留刚才的任务和验收函数：

```python
budget = Budget(token_limit=10_000)
model = BudgetModel(model, budget)
report = evaluate(model, root)
```

[阶段 budget.py](../lessons/stages/15/minicode/budget.py)的非流式逻辑是：

```python
def complete(self, messages, tools=()):
    self.budget.check()
    reply = self.inner.complete(messages, tools)
    self.budget.record(reply.usage)
    return reply
```

现在再解释它的限制：这是下一次请求前的软上限，当前请求可能超额；缺少 usage 的请求不能恢复完整账单。流式结果在最终 `done` 上累计一次。父子 Agent 共用一个预算对象，摘要请求也经过预算边界：

```text
CompactingModel → BudgetModel → 协议适配器
```

## 4. 从一个任务扩展到一组任务

先把输入、目标和验收函数从 `evaluate()` 中提成参数，再逐项运行。每项使用独立的初始目录，避免上一项修改影响下一项。

| 任务 | 独立验收 |
| --- | --- |
| 修改一个常量 | 指定文件内容符合预期 |
| 查找函数调用 | 返回的路径和行号确实存在 |
| 修复一个错误 | 原有测试和复现错误的用例通过 |
| 写入被拒绝 | 对应文件没有产生，结果说明了拒绝 |

重复执行真实任务，记录模型、协议、任务版本和参数，再统计通过率。固定剧本只证明评测程序接线正确，不能作为模型成功率。

[evals.py](../minicode/evals.py)提供三个确定的离线回归案例：正常编辑、拒绝写入和未知工具。掌握真实任务的验收后，再用这些剧本检查验收程序是否会被一句“完成”误导。

## 5. 再把失败分开

| 结果 | 含义 |
| --- | --- |
| `success` 但验收失败 | 模型结束了任务，实际目标没有达成 |
| `max_turns` | 没在限定轮数内结束 |
| `length` | 输出截断，不能执行半成品调用 |
| `refusal` | 模型拒绝，不是网络故障 |
| `error` | 继续查看认证、传输、协议或执行器错误 |
| `cancelled` | 任务被中断，检查未完成调用的状态 |

内核测试、协议测试与真实任务评测各自验证不同内容。最终费用记录在[第 18 章](18-runtime.md)加入；这里先累计服务商返回的 token。

## 验收与练习

```bash
uv run python examples/15_evals.py --step minimal --offline
uv run python examples/15_evals.py --step enhanced --offline
uv run python -m pytest tests/test_lesson15.py tests/test_budget.py tests/test_evals.py -q
uv run python -m minicode.evals
```

把离线剧本改成只回答“完成”，确认 `status=success` 但 `passed=false`。再让 Edit 的旧字符串不匹配，检查失败能否反映在最终文件验收里。最后添加一个真实任务，先写验收函数再运行模型。
