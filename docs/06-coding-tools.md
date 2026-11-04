# 第 6 章：让 Agent 读代码、修改代码和运行检查

先回到教程仓库根目录，执行下面的导出命令；随后进入独立的本章目录。手写时保留自己的练习目录，用导出版本核对文件清单和差异：

```bash
uv run python scripts/check_tutorial.py --chapter 06 --export /tmp/minicode-ch06
cd /tmp/minicode-ch06
uv sync
```

以下运行和测试命令在本章练习目录中执行。目录只含第 00–06 章的代码与测试；文件增量由[阶段清单](../lessons/stages.json)记录。导出目标必须为空，不会覆盖已有练习。

上一章已经证明：同一个循环可以接三种模型协议。现在只替换工具集合，让它完成一次真正的代码修改。

这一章增加 [coding_tools.py](../minicode/coding_tools.py)，保持 `Agent` 和三个协议适配器不变。
你将完成六个工具，并用假模型执行“读取 → 修改 → 验证”。

## 本章文件清单

保留上一章全部文件，新建 [minicode/coding_tools.py](../minicode/coding_tools.py)，新增[实验入口](../examples/06_coding_tools.py)和[本章测试](../tests/test_coding_tools.py)。这些链接是本章完整文件；导出目录中的同名文件内容一致。正文随后展示的是完整文件中的解释片段，不是省略了导入后还能单独运行的脚本。

完成后运行本章命令，再执行 `uv run python -m pytest tests -q`，确认已有能力没有回退。不要把前面建立的模型、工具或权限对象全部丢掉重建；只在下文指定的位置增加本章能力。

## 1. 先明确工具的共同边界

第 2 章建立了三种不同的对象：

- `ToolSpec` 是模型看到的说明书。
- `ToolCall` 是模型提出的调用。
- `Tool` 的执行函数接收参数和 `ToolContext`，返回内容或 `ToolResult`。

服务商的 `function`、`input_schema` 等字段都不进入文件工具。
工具返回错误时，`ToolRunner` 保留 `is_error`，再由模型适配器决定如何编码。

我们先实现这六件事：

| 工具 | 参数 | 结果 | 是否只读 |
| --- | --- | --- | --- |
| Read | `path` | 带行号的 UTF-8 文本 | 是 |
| Write | `path`、`content` | 写入结果 | 否 |
| Edit | `path`、`old`、`new` | 精确替换结果 | 否 |
| Glob | `pattern` | 相对路径列表 | 是 |
| Grep | `text` | 路径、行号、匹配行 | 是 |
| Bash | `command`、可选 `timeout` | 输出和退出码 | 否 |

`read_only` 表示工具会不会修改状态，`parallel_safe` 表示是否适合并发。
这两个属性分别保留。本章的循环仍然串行执行；有属性不等于已经实现调度器。

## 2. 所有文件路径从同一个地方解析

如果每个工具都自己处理路径，Read 可能允许某个位置，Edit 却使用另一个位置。
因此先写公共函数：

```python
def resolve_inside(ctx, name):
    root = ctx.cwd.resolve()
    path = Path(name).expanduser()
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root):
        raise ValueError("路径超出工作目录")
    return path
```

`resolve()` 会处理 `..` 和已有的符号链接。
不能只检查字符串是否以工作目录开头：`/work-extra` 也以 `/work` 开头。

本章文件工具会拒绝指向工作目录外的链接。
搜索时不跟随符号链接，也跳过 `.git`、`.venv`、`node_modules` 等目录。
这样，模型先用 Glob 找文件，再用 Read 明确读取它。

这是一组文件操作检查，不是操作系统沙箱。
并发进程仍可能在检查和使用之间替换路径，Bash 也不受这组路径函数约束。

## 3. Read 记录内容版本，Write 和 Edit 检查版本

模型读完一个文件后，用户可能已经修改了它。
此时模型若按照旧内容覆盖，就会丢失用户的工作。

`build_coding_tools()` 创建一个 `seen` 字典，记录已读文件的 SHA-256。
Read 读取字节后记录摘要；Write 和 Edit 修改已有文件前重新计算摘要。

```python
def fresh(path):
    data = _read(path)
    if path not in seen:
        raise ValueError("修改已有文件前必须先 Read")
    if seen[path] != hashlib.sha256(data).hexdigest():
        raise ValueError("文件内容已经改变，请重新 Read")
    return data.decode("utf-8")
```

只比较修改时间不够：内容可能改变后恢复时间戳，或在文件系统时间精度内被修改。
这里检查内容本身。一次工具调用中的检查与修改还共用一把本地锁。

Write 可以直接创建新文件。覆盖已有文件必须先读取。
Edit 要求 `old` 非空，且在文件中恰好出现一次：

```python
count = text.count(old)
if count != 1:
    raise ValueError(f"old 必须唯一，实际出现 {count} 次")
updated = text.replace(old, new, 1)
```

零次说明原文不匹配，多次说明上下文不够准确。
这类错误交回模型后，它可以重新 Read，再提交更明确的修改。
工具执行器不尝试“猜测”模型想改哪一处。

每个会话调用一次 `build_coding_tools()`，不要让无关会话共享 `seen`。
自己成功写入的内容也会更新摘要，因此连续 Edit 可以继续工作。

## 4. 搜索和输出都要有上限

Glob 使用 `Path.match()` 的相对路径匹配，Grep 使用字面字符串搜索。
它们不是完整的 ripgrep 替代品；这里先把“查找文件”和“查找内容”讲清楚。

实现采用几条固定限制：

- 单个文本文件最多读取 1,000,000 字节。
- Glob 最多返回 500 个匹配文件。
- Grep 最多返回 100 条匹配行。
- 工具输出最多保留约 20,000 个字符，并标明省略位置。

Read 的输出带行号，便于解释位置；Edit 接收原始文本，不接收行号前缀。
长输出保留开头和结尾，因为检查失败的信息常在最后。
输出被截断后，模型不能声称已看过被省略的部分。

## 5. Bash 的返回值必须包含失败状态

执行命令不是“拿到 stdout 就算成功”。还需要处理标准错误、退出码和超时。

```python
proc = subprocess.Popen(
    command, shell=True, cwd=ctx.cwd,
    stdin=subprocess.DEVNULL,
    stdout=output_file, stderr=subprocess.STDOUT,
    start_new_session=os.name == "posix",
)
proc.wait(timeout=timeout)
```

`stdin=DEVNULL` 防止命令等待交互输入。
输出写进临时文件，展示时只读取有限的头尾内容。
超时或用户中断后，在 POSIX 系统中终止整个进程组，并等待进程退出。
Windows 分支只能保证终止直接子进程。

执行函数还不知道本次工具调用 ID，所以返回：

```python
ToolResult("", text, is_error=proc.returncode != 0)
```

`ToolRunner.run()` 用实际 `call.id` 重建结果，ID 配对不会由每个工具自行猜测。
超时也返回错误，并明确说明进程被终止。

`cwd` 只是命令的初始工作目录。shell 可以访问其他路径、启动程序和发起网络请求。
第 7 章会加入审批；需要运行不可信命令时，还要在容器或其他隔离环境中运行 Agent。

## 6. 跑完一次修改闭环

运行 [本章示例](../examples/06_coding_tools.py)：

```bash
uv run python examples/06_coding_tools.py
```

示例在临时目录创建 `answer.py`，内容为 `answer = 41`。
假模型的剧本是：

```python
FakeModel([
    call("Read", path="answer.py"),
    call("Edit", path="answer.py", old="41", new="42"),
    call("Bash", command="python -c 'from answer import answer; assert answer == 42'"),
    "已把答案改成 42，断言通过。",
])
```

实际示例使用当前 Python 解释器的绝对路径，避免系统 `python` 指向另一套环境。
输出中应看到原文件、修改成功、退出码 0 和最终 `success`。
本章只运行仓库自带的固定剧本；下一章才把这些有副作用的工具交给真实模型调用。

## 7. 用失败用例检查工具

```bash
uv run python -m pytest tests/test_coding_tools.py -q
```

[测试文件](../tests/test_coding_tools.py) 检查：

- 没有读取就 Edit，会拒绝。
- 文件内容变化，即使时间戳不变，也会拒绝覆盖。
- `../`、绝对外部路径和外部符号链接都不能通过文件工具访问。
- `old` 不唯一时不修改；搜索不进入 `.git`。
- Bash 非零退出码、超时和长输出能正确返回。

模型可能决定错误的动作，但每个动作仍须符合工具的确定规则。
这是工具实现可以离线验证的部分。

## 练习

1. 给 Edit 增加 `replace_all` 参数，同时保留默认唯一匹配行为。
2. 为 Read 增加分段读取，说明部分读取是否足以允许覆盖整个文件。
3. 设计一个测试：模型执行 Bash 后文件发生变化，下一次 Edit 应要求重新 Read。

参考 [Codex 工具路由](https://github.com/openai/codex/blob/93b4e5b4c6474cdbf12614c4ac3acbd006fbee89/codex-rs/core/src/tools/router.rs) 的声明与执行分离。本章采用更小的 Python 实现，参数与文件限制由 minicode 自己定义。

[上一章：Anthropic Messages](05-anthropic.md) · [目录](README.md) · [下一章：权限](07-permissions.md)
