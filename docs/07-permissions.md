# 第 7 章：把权限放在工具执行之前

先回到教程仓库根目录，执行下面的导出命令；随后进入独立的本章目录。手写时保留自己的练习目录，用导出版本核对文件清单和差异：

```bash
uv run python scripts/check_tutorial.py --chapter 07 --export /tmp/minicode-ch07
cd /tmp/minicode-ch07
uv sync
```

以下运行和测试命令在本章练习目录中执行。目录只含第 00–07 章的代码与测试；文件增量由[阶段清单](../lessons/stages.json)记录。导出目标必须为空，不会覆盖已有练习。

上一章的工具已经能写文件和执行命令。现在需要决定：模型提出一次调用后，什么时候可以执行？

本章增加 [permissions.py](../minicode/permissions.py)，把权限检查包装在 `ToolRunner` 外面。
模型适配器和 Agent 循环不变。
本章结束时，同一组工具可以用于交互模式、无人值守模式和只读模式。

## 本章文件清单

保留上一章全部文件，新建 [minicode/permissions.py](../minicode/permissions.py)，新增[实验入口](../examples/07_permissions.py)和[本章测试](../tests/test_permissions.py)。这些链接是本章完整文件；导出目录中的同名文件内容一致。正文随后展示的是完整文件中的解释片段，不是省略了导入后还能单独运行的脚本。

完成后运行本章命令，再执行 `uv run python -m pytest tests -q`，确认已有能力没有回退。不要把前面建立的模型、工具或权限对象全部丢掉重建；只在下文指定的位置增加本章能力。

## 1. 请求执行与授权执行是两件事

`ToolCall` 只表示“模型想做什么”，不能证明用户已经同意。
因此权限代码不读取模型给出的“用户已授权”等文字，也不让工具自己决定能否跳过审批。

执行链变成：

```text
Agent
  → PermissionRunner.run(ToolCall)
  → ToolRunner.run(ToolCall)
  → Tool.execute(arguments, context)
  → ToolResult
```

两个 runner 公开相同的方法：

```python
runner.specs()       # 返回模型可以看到的工具定义
runner.run(call)     # 返回对应 call.id 的 ToolResult
```

包装器还转发 `tools` 和 `context`，供后续的 hooks、规划和子 Agent 组合使用。
Agent 无须知道包装了几层。

## 2. 先实现三个明确的决定

权限规则从最小可用的三种情况开始：

| 情况 | 行为 |
| --- | --- |
| 本地工具标为只读 | 直接执行 |
| 工具名在显式 `allowed` 集合中 | 直接执行 |
| 其余工具 | 调用审批函数；没有审批函数则拒绝 |

`allowed` 匹配完整工具名，不使用 shell 命令前缀或模糊子串。
例如 `allowed=["Write"]` 允许该工具的所有合法调用，不是只允许某个路径。
文件路径仍由第 6 章的工具检查。
允许 `Bash` 则意味着允许本次进程执行任意通过 schema 校验的 shell 命令。

这里不尝试用字符串拆分证明某个 shell 命令“安全”。
需要更细的约束时，可以新增专用工具，例如只执行固定测试命令的 `RunTests`。

核心分支可以直接读成规则：

```python
if self.read_only and not tool.read_only:
    return ToolResult(call.id, "只读模式不允许执行这个工具", True)
if tool.read_only or call.name in self.allowed:
    return self.inner.run(call)
if not self.approval.ask(call.name, call.arguments):
    return ToolResult(call.id, "用户未授权此次操作", True)
return self.inner.run(call)
```

完整实现先检查工具名和参数。
参数缺字段、类型不对或 JSON 解析失败时，直接交给工具执行器返回校验错误，不先打扰用户。

## 3. 审批是一个共享对象

命令行中的审批函数只负责展示具体动作并收集回答：

```python
def ask(name, arguments):
    print(name, arguments)
    return input("允许执行？[y/N] ").strip().lower() == "y"

approval = Approval(ask)
runner = PermissionRunner(inner, approval)
```

只有布尔值 `True` 表示同意。
没有回答、其他字符串和未配置回调都不会自动转成同意。
拒绝也会成为一条工具错误结果，让模型知道这次调用没有执行。

多个 Agent 可能同时请求审批。
如果每个 Agent 只锁自己的实例，它们仍然可以同时调用 `input()`。
因此锁属于共享的 `Approval`：

```python
class Approval:
    def __init__(self, callback=None):
        self.callback = callback
        self._lock = RLock()

    def ask(self, name, arguments):
        with self._lock:
            if self.callback is None:
                return False
            return self.callback(name, arguments) is True
```

后面创建子 Agent 时，传递这个对象；不要重新创建一个拥有独立锁的审批对象。
这里串行化的是用户交互，不是全部工具执行。

## 4. 只读模式需要双重检查

只读模式首先从 `specs()` 隐藏可写工具，让模型不再看到 Write、Edit 和 Bash。
但仅从工具列表删除还不够：历史消息或错误的模型输出仍可能携带这些调用。

`run()` 因此再次检查 `tool.read_only`。
只读限制优先于 `allowed` 和审批，不能通过 `allowed=["Bash"]` 绕过。

```python
plan_runner = PermissionRunner(inner, read_only=True)
print([spec.name for spec in plan_runner.specs()])
# ['Read', 'Glob', 'Grep']
```

`read_only` 和 `parallel_safe` 仍是不同的属性。
把一个会启动可写子 Agent 的工具标成只读，会破坏这条边界。
第 12 章的初版子 Agent 只获得只读工具，不通过名称伪装获得写入权限。

## 5. 交互与无人值守共用执行层

交互模式提供 `Approval(ask)`，在必要时询问。
无人值守模式没有人回答，就用默认的 `Approval()`。

```python
interactive = PermissionRunner(inner, Approval(ask))
unattended = PermissionRunner(inner)
known_workflow = PermissionRunner(inner, allowed=["Write"])
```

真实调用时，应由用户通过 CLI 显式选择允许的工具。
项目文件里的自然语言不应替用户添加 allow 规则。
我们也不自动执行项目中的 hooks 或 MCP 启动命令；后续章节会保留明确的加载入口。

权限层是执行约束，提示词中的“不要绕过拒绝”只是给模型的行为说明。
后者不能代替前者。
如果同时允许 Bash 和拒绝 Write，Bash 仍有写文件的能力；规则应按实际能力理解。

## 6. 跑三个决定

```bash
uv run python examples/07_permissions.py
```

[示例](../examples/07_permissions.py) 在临时目录运行，并输出：

```text
默认拒绝: True 文件存在: False
显式允许: True 文件内容: hello
只读模式工具: Read, Glob, Grep
```

它先发出同一次 Write 调用，确认默认拒绝没有创建文件。
随后明确允许 Write，验证文件出现。
最后即使 `allowed` 含 Bash，只读模式也不会公布它。

本章新增的是执行条件，不需要重写 Read、Write，也不需要为三种模型协议各写一套权限逻辑。

## 7. 检查拒绝路径和并发提问

```bash
uv run python -m pytest tests/test_permissions.py -q
```

[测试](../tests/test_permissions.py) 验证默认拒绝、精确允许、只读限制和无效参数不提问。
还有一个并发测试：四个 runner 共用一个 Approval，同时请求写入。
回调记录正在运行的审批数量，最大值应始终为 1。

这是一次能定位原理的测试：如果把锁放回每个 runner，最大值就可能大于 1。
不需要真实终端输入，也不需要模型 API。

## 练习

1. 新增 `RunTests` 工具，执行用户配置的固定命令，并把它与通用 Bash 的权限区分开。
2. 为审批结果增加原因，展示到工具结果和日志中。
3. 为 Write 增加仅允许新文件的规则，并覆盖“文件已存在”的测试。

参考 [Codex 工具路由](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/core/src/tools/router.rs) 的集中执行入口。本章没有复刻完整沙箱、组织策略或目录信任系统；本地约束的范围以代码和测试为准。

[上一章：编码工具](06-coding-tools.md) · [目录](README.md) · [下一章：流式输出](08-streaming.md)
