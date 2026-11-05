# 第 6 章：让 Agent 读文件、修改并检查

[主线前章：Chat Completions](03-chat-completions.md) · [目录](README.md) · [下一章：权限](07-permissions.md)

前面已经完成模型与工具的往返。现在把加法函数换成文件操作，让模型根据读到的内容决定修改，再用检查结果判断任务是否完成。第 04、05 章是协议选修，不需要先学完。

先在教程仓库根目录导出本章，再进入练习目录：

```bash
uv run python scripts/check_tutorial.py --chapter 06 --export /tmp/minicode-ch06
cd /tmp/minicode-ch06
uv sync
```

导出目标须为空或尚不存在。以下命令在练习目录执行；真实调用沿用第 00 章的环境变量配置。

## 1. 先完成一个文件任务

```bash
uv run python examples/06_coding_tools.py
```

程序创建临时练习目录，写入内容为 `41` 的 `answer.txt`。真实模型只能使用三个工具：

| 工具 | 做什么 |
| --- | --- |
| Read | 读取这个固定文件 |
| Edit | 将文件中唯一匹配的一段文字替换掉 |
| RunTests | 检查文件内容是否为 42 |

应看到读取结果、修改结果、`检查通过：answer.txt 是 42` 和模型回答。末尾独立打印的 `最终文件：42` 来自 Python 读取文件。练习结束会清理目录。

这一版只有一个固定文件，没有让模型选择文件路径或输入 shell 命令。完整工具在 [coding_prototype.py](../lessons/coding_prototype.py)，运行入口在 [06_coding_tools.py](../examples/06_coding_tools.py)。

## 2. 先写三个普通函数

第一步只是读文件。工具函数接收第 02 章已有的参数对象和上下文：

```python
def read_answer(args, context):
    return (context.cwd / "answer.txt").read_text(encoding="utf-8")
```

第二步增加修改。模型给出 `old` 和 `new`，Python 才执行替换：

```python
def edit_answer(args, context):
    path = context.cwd / "answer.txt"
    text = path.read_text(encoding="utf-8")
    if not args["old"] or text.count(args["old"]) != 1:
        raise ValueError("old 必须在文件中恰好出现一次")
    path.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
    return "已修改 answer.txt"
```

这里保留唯一匹配要求，因为它直接决定一次 Edit 的含义。最后加一个固定检查：

```python
def check_answer(args, context):
    passed = read_answer({}, context).strip() == "42"
    return ToolResult("", "检查通过：answer.txt 是 42" if passed else "检查失败：answer.txt 应为 42",
                      is_error=not passed)
```

检查读取修改后的文件，不根据模型最后说了什么判断成功。`ToolRunner` 会为这个结果补上实际调用 ID。

这些是 [coding_prototype.py](../lessons/coding_prototype.py)中的完整函数定义；所需导入和工具声明见同一文件。先把三个函数写出来，再用上一章的 `ToolSpec + Tool` 注册。Read、RunTests 标记只读，Edit 标记可写。

## 3. 把函数接进已有循环

在临时目录创建文件后，组装已有对象：

```python
runner = ToolRunner(build_prototype_tools(), ToolContext(root))
agent = Agent(model, runner, max_turns=6)
for event in agent.run("先 Read answer.txt，再 Edit 将 41 改成 42，最后调用 RunTests 检查。"):
    print_event(event)
```

`model` 来自第 00、03 章已配置的真实接口，`root` 是本次练习目录。循环没有因为工具换成文件操作而改变：

```text
模型请求 Read → 文件内容回到历史
模型请求 Edit → 修改结果回到历史
模型请求 RunTests → 检查结果回到历史
模型回答任务结果
```

试着把目标改成 43，保留检查条件为 42。观察 RunTests 失败以后，模型收到什么，是否会重新检查或修改。真实模型的选择可能不同，正好用历史解释它下一步的依据。

## 4. 需要操作多个文件时，再扩展工具

现在给 Read 和 Edit 加 `path` 参数。使用[本章完整编码工具](../lessons/stages/06/minicode/coding_tools.py)替换固定文件版本，运行第二个检查点：

```bash
uv run python examples/06_coding_tools.py --step enhanced
```

这个检查点仍在临时目录做同一任务，采用完整 Read、Edit，保留固定 RunTests。比较模型的调用：现在必须指定 `path="answer.txt"`。再读增强版，逐项回答新增规则解决什么问题：

| 遇到的情况 | 新增规则 |
| --- | --- |
| 模型指定不同路径 | `resolve_inside()` 将文件限制在工作目录内 |
| Read 以后文件被别人改过 | 记录内容摘要，Write/Edit 前重新核对 |
| 搜索结果太多 | Glob、Grep 分别限制结果数量 |
| 文件或输出过长 | 限制读取量，截断时注明省略内容 |

路径解析使用 `Path.resolve()` 与 `is_relative_to()`，不使用字符串前缀。已存在的符号链接也参与解析；搜索不跟随链接，并跳过 `.git`、`.venv`、`node_modules`。

摘要记录放在每次 `build_coding_tools()` 创建的 `seen` 中。读后再写才需要“已读版本”的概念：覆盖已有文件前检查 SHA-256，发生变化就要求重新 Read；自己成功写入后更新摘要。检查和修改共用本地锁。到这里再学习这些细节，才能看清它们保护的是哪个动作。

## 5. 再加入搜索、创建文件与通用命令

完整实现共提供六种工具：

| 工具 | 参数 | 结果 |
| --- | --- | --- |
| Read | path | 带行号的文本 |
| Write | path、content | 创建或覆盖结果 |
| Edit | path、old、new | 精确替换结果 |
| Glob | pattern | 匹配文件路径 |
| Grep | text | 路径、行号、匹配行 |
| Bash | command、可选 timeout | 输出、退出码与失败状态 |

先用 Glob 找文件，再 Read；先让模型提交 Edit，再用检查验证。工具的 `read_only` 与 `parallel_safe` 是不同属性，本章循环仍串行执行。

最小原型的 RunTests 只检查固定条件。真正需要运行项目测试时，再加入 Bash。它用 `subprocess.Popen()` 运行命令，收集 stdout、stderr 和退出码，超时则终止进程。非零退出码要成为工具错误结果；输出太长时保留有限头尾。POSIX 上清理进程组，Windows 分支只保证终止直接子进程。

通用 Bash 的 `cwd` 只是起始目录，不会限制它访问其他路径。先学下一章的审批，再将通用写入和 Bash 开放给真实模型；第 17 章继续加入操作系统沙箱。本章默认真实实验只开放上述临时目录中的固定任务。

## 6. 对两个检查点验收

```bash
uv run python examples/06_coding_tools.py --offline
uv run python examples/06_coding_tools.py --step enhanced --offline
uv run python -m pytest tests/test_coding_tools.py -q
```

`--offline` 用固定调用序列复现同一流程；默认运行才由真实模型决定调用。先确认文件变为 42 且检查通过，再检查以下失败条件：Edit 未匹配或匹配多处、读取后文件变化、路径越界、命令非零退出或超时。

练习：先增加一个固定的 `ReadRequirements`，让模型根据第二个文件确定目标；再推广到带路径的 Read。每次只扩展一个能力，观察它给循环增加了什么信息。

本章新增 [coding_prototype.py](../lessons/coding_prototype.py)和[实验入口](../examples/06_coding_tools.py)，增强版及测试见[阶段清单](../lessons/stages.json)。下一章在工具执行前加一个确认点。
