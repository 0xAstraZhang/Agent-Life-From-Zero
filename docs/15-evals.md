# 第 15 章：亲手给 Agent 的任务写验收

上一章：[MCP](14-mcp.md) · 下一章：[命令行入口](16-shipping.md)

继续在自己的 `my-agent/` 中写代码。本章先做一项真实任务：把文件中的 41 改成 42，由独立的 Python 判断检查结果。再把同一个验收过程扩成两个相互隔离的任务。

我们只复用前面写出的 `Agent` 和 `model.open_model()`。本章没有依赖尚未编写的命令行程序，也没有预先规定模型必须怎样回答的假模型。

## 1. 先写任务与验收函数

新建 `evaluation.py`，完整写入：

```python file=evaluation.py step=minimal
from agent import Agent


def evaluate(model, root, initial=41, desired=42):
    root.mkdir(parents=True, exist_ok=True)
    target = root / "answer.py"
    target.write_text(f"answer = {initial}\n", encoding="utf-8")
    calls = []

    def read_answer():
        calls.append("ReadAnswer")
        return target.read_text(encoding="utf-8")

    def set_answer(value):
        calls.append("SetAnswer")
        target.write_text(f"answer = {value}\n", encoding="utf-8")
        return target.read_text(encoding="utf-8")

    tools = {
        "ReadAnswer": {
            "spec": {"type": "function", "function": {
                "name": "ReadAnswer", "description": "读取本次任务的 answer.py。",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            }},
            "execute": read_answer,
            "read_only": True,
        },
        "SetAnswer": {
            "spec": {"type": "function", "function": {
                "name": "SetAnswer", "description": "把本次任务文件的 answer 改成给定整数。",
                "parameters": {
                    "type": "object", "properties": {"value": {"type": "integer"}},
                    "required": ["value"], "additionalProperties": False,
                },
            }},
            "execute": set_answer,
            "read_only": False,
        },
    }
    answer = Agent(model, tools).run(
        f"先用 ReadAnswer 读取文件，再用 SetAnswer 把 answer 改为 {desired}。完成后说明结果。"
    )
    file_ok = target.read_text(encoding="utf-8") == f"answer = {desired}\n"
    actions_ok = ("ReadAnswer" in calls and "SetAnswer" in calls
                  and calls.index("ReadAnswer") < calls.index("SetAnswer"))
    return {
        "initial": initial, "desired": desired,
        "file_passed": file_ok, "actions_passed": actions_ok,
        "passed": file_ok and actions_ok, "answer": answer,
    }
```

这两个工具只操作本次临时目录中的一个文件，不能选择其他路径。本章明确允许这项小修改。

先读函数末尾：验收直接打开文件，检查它是否真的改变；还检查指定的动作是否发生。模型最后一句话只是记录，不能决定 `passed`。

再回头读前面的工具定义。它们使用第 02–03 章已经写过的注册表格式，没有引入新的 Agent 机制。

## 2. 运行一个真实任务

替换 `demo.py`：

```python file=demo.py step=minimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluation import evaluate
from model import open_model

with TemporaryDirectory() as directory, open_model() as model:
    report = evaluate(model, Path(directory))
    print(json.dumps(report, ensure_ascii=False))
    if not report["passed"]:
        raise RuntimeError("任务未通过独立验收")
```

运行 `uv run python demo.py`。预期报告中 `file_passed`、`actions_passed` 和 `passed` 都为 `true`。

现在把任务改成另一个目标数，同时改变验收的 `desired`。验收判断必须来自任务要求，不能从模型回答里提取一个数字再拿它当答案。

## 3. 增强：把一个验收扩成一组任务

新建 `evaluation_suite.py`：

```python file=evaluation_suite.py step=enhanced
from evaluation import evaluate


def evaluate_suite(model, root):
    reports = []
    tasks = [(41, 42), (99, 100)]
    for index, (initial, desired) in enumerate(tasks):
        directory = root / f"task-{index}"
        report = evaluate(model, directory, initial=initial, desired=desired)
        reports.append(report)
    passed = sum(report["passed"] for report in reports)
    return {"passed": passed, "total": len(reports), "reports": reports}
```

每次 `evaluate()` 都创建一个新的 Agent 和新的目录。前一个任务的消息、文件修改不会进入后一个任务。

替换 `demo.py`：

```python file=demo.py step=enhanced
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluation_suite import evaluate_suite
from model import open_model

with TemporaryDirectory() as directory, open_model() as model:
    report = evaluate_suite(model, Path(directory))
    print(json.dumps(report, ensure_ascii=False))
    print(f"任务通过：{report['passed']}/{report['total']}")
    if report["passed"] != report["total"]:
        raise RuntimeError("有任务未通过验收")
```

再次运行 `uv run python demo.py`。先看每一项的文件检查，再看汇总的 `2/2`，不要只保留一个总通过率。

## 4. 看到失败以后再增加分类

到这里，我们手写了任务初始状态、工具、真实运行、独立验收、隔离目录和多任务汇总。下一步可以按遇到的问题扩展：

| 现象 | 下一项检查 |
| --- | --- |
| 模型说完成，文件没变 | `file_passed` 应为 false |
| 文件变了，但跳过要求的读取 | `actions_passed` 应为 false |
| 模型请求失败 | 单独记录连接、认证或协议错误 |
| 达到 Agent 轮数上限 | 记录任务未完成，而不是给文件验收补一个成功 |
| 多次运行结果不同 | 保留每次报告，统计波动 |

添加新的任务时，先写确定的验收条件，再让模型尝试。代码修复任务可以运行测试；查找任务可以核对路径和行号；拒绝写入任务可以检查文件没有产生。

第 18 章将手写请求计时、连接重试和共同期限。服务商 token 预算与费用换算属于[最终实现参考](18-runtime-reference.md)，本章没有通过一次示例就实现这些能力。

## 自检

把 `evaluate()` 中的任务临时改成“不要调用工具，只回答完成”，其他代码保持不变。运行后应失败，而不是因为文本里有“完成”就通过。完成这个反例后恢复任务。

离线回归可以为 HTTP 返回固定响应，验证验收程序本身。真实模型效果仍然以你刚才实际运行的任务结果为准。仓库中的[离线评测参考](../minicode/evals.py)可以在完成手写版后对照。
