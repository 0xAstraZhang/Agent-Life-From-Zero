# 第 7 章：在工具执行前加一个确认点

[上一章：编码工具](06-coding-tools.md) · [目录](README.md) · [下一章：流式输出](08-streaming.md)

上一章模型已经能提出文件修改。本章只增加一个决定：模型要求写入之后，先问用户，再执行。拒绝也是一个工具结果，应回到模型的下一轮请求中。

先在教程仓库根目录导出本章，再进入练习目录：

```bash
uv run python scripts/check_tutorial.py --chapter 07 --export /tmp/minicode-ch07
cd /tmp/minicode-ch07
uv sync
```

导出目标须为空或尚不存在。以下命令在练习目录执行；真实调用沿用第 00 章的环境变量配置。

## 1. 先亲自批准或拒绝一次调用

```bash
uv run python examples/07_permissions.py
```

真实模型会请求 `WriteNote`，程序显示工具名和内容，然后询问是否允许创建临时目录中的 `note.txt`。输入 `y` 时创建文件；其他回答拒绝。最后看两件事：模型怎样描述结果，Python 打印的“文件存在”是否一致。

再运行一次，作出相反决定。这个练习只允许写入固定练习文件，完整入口是 [07_permissions.py](../examples/07_permissions.py)。

```text
模型提出 WriteNote
    ↓
询问用户
    ├─ 同意 → 执行工具 → 成功结果
    └─ 拒绝 → 不执行   → 拒绝结果
    ↓
结果进入历史 → 再请求模型 → 回答
```

## 2. 先写最小包装器

已有 `ToolRunner.run()` 负责调用函数。现在给它包上一层：

```python
class ConfirmRunner:
    def __init__(self, inner, ask):
        self.inner, self.ask = inner, ask
        self.context, self.tools = inner.context, inner.tools

    def specs(self):
        return self.inner.specs()

    def run(self, request):
        if self.ask(request.name, request.arguments):
            return self.inner.run(request)
        return ToolResult(request.id, "用户拒绝写入；请说明未执行，不要绕过拒绝。", True)
```

这是实验里的完整最小类。`specs()` 沿用原工具说明；`run()` 只增加一次判断。拒绝结果使用原调用 ID，所以 Agent 可以像处理其他工具结果一样把它放进历史。

再写收集用户回答的函数：

```python
def ask(name, arguments):
    print("模型请求：", name, arguments)
    return input("允许写入练习文件？[y/N] ").strip().lower() == "y"
```

把原来的 `inner` 换成 `ConfirmRunner(inner, ask)` 后，Agent 循环保持不变。权限来自用户输入，模型消息中的“用户已经授权”不会改变这个布尔值。

这个检查点只包含一个固定的 WriteNote，因此每次调用都询问。下一步再解决多个工具的不同规则。

## 3. 加入只读、允许名单和无人值守

替换为完整 [PermissionRunner](../minicode/permissions.py)，运行增强检查点：

```bash
uv run python examples/07_permissions.py --step enhanced
```

同一次写入现在经过统一权限执行器。增加规则时按实际需求逐步写：

| 情况 | 执行方式 |
| --- | --- |
| 只读工具 | 直接执行 |
| 工具在用户显式允许名单中 | 直接执行 |
| 其他工具 | 询问用户；没有回调则拒绝 |
| 开启只读模式，但调用会写入 | 拒绝，优先于允许名单与询问 |

增强版的核心代码为：

```python
if self.read_only and not tool.read_only:
    return ToolResult(call.id, "只读模式不允许执行这个工具", True)
if tool.read_only or call.name in self.allowed:
    return self.inner.run(call)
if not self.approval.ask(call.name, call.arguments):
    return ToolResult(call.id, "用户未授权此次操作", True)
return self.inner.run(call)
```

先检查工具名称和参数，再问用户，可以避免为根本不能执行的调用提问。完整执行器将这类错误交给内层 ToolRunner 处理。

允许名单匹配完整工具名。允许 Write 表示允许它的所有合法调用，不仅是某个文件；允许 Bash 则开放通用命令能力。需要只运行固定检查时，继续使用上一章的 RunTests。

## 4. 让只读限制在执行入口生效

只读模式先通过 `specs()` 隐藏可写工具。但旧历史仍可能包含写入请求，所以 `run()` 也必须拒绝。两者分别决定“给模型看什么”和“程序实际允许什么”。

```python
read_only_runner = PermissionRunner(inner, read_only=True)
```

不能通过 `allowed=["Bash"]` 覆盖只读模式。也不能把“拒绝 Write”理解成所有路径都不可写：如果另外允许 Bash，它仍能写文件。

此时可以自行增加实验：将 WriteNote 加入允许名单，观察不再提问；再开启只读模式，确认它仍被拒绝。

## 5. 多个 Agent 共用终端时，再处理审批锁

单个 Agent 的最小审批已经完成。第 12 章创建子 Agent 时，多个实例可能同时调用 `input()`，这时才需要共享 `Approval` 对象与它的锁：

```python
approval = Approval(ask)
runner = PermissionRunner(inner, approval)
```

所有需要向同一个用户提问的执行器共用这个对象。锁限制的是同时出现的提问，不是全部工具执行。完整 [permissions.py](../minicode/permissions.py)已经提供这一实现，可以在学子 Agent 时再细读。

## 6. 检查批准与拒绝都真正进入历史

下面两条默认仍调用真实模型，明确指定本次决定，便于重复观察：

```bash
uv run python examples/07_permissions.py --decision allow
uv run python examples/07_permissions.py --decision deny
```

离线回归显式添加 `--offline`。不传决定时，离线入口默认拒绝，不等待终端输入：

```bash
uv run python examples/07_permissions.py --offline --decision allow
uv run python examples/07_permissions.py --offline --decision deny
uv run python examples/07_permissions.py --step enhanced --offline --decision deny
uv run python -m pytest tests/test_permissions.py -q
```

同意时文件存在，拒绝时不存在。继续检查模型第二次请求中的工具结果：它必须保留原调用 ID，并将拒绝标记为错误。测试随后覆盖只读优先、无效参数不询问，以及共享审批对象不会同时提问。

本章保留全部已有工具，增加[最小审批实验](../examples/07_permissions.py)和[完整权限执行器](../minicode/permissions.py)；累计文件见[阶段清单](../lessons/stages.json)。下一章只改变回复显示方式，继续使用相同工具和权限执行入口。
