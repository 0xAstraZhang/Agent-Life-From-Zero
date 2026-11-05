# 第 9 章：把项目约定放进模型的上下文

[上一章：流式输出](08-streaming.md) · [目录](README.md) · [下一章：会话与压缩](10-sessions-compaction.md)

模型不会自动知道项目怎样测试。本章只增加一个动作：读取项目约定，把它放进下一次请求的 system 消息。先看到这条信息怎样影响回答，再处理文件边界。

## 1. 先读取一份已知的说明

在练习目录放一个 `MINICODE.md`：

```markdown
回答以“项目约定：”开头。
测试命令是 python -m pytest。
```

已知文件存在时，最小实现只有读取和拼接两步。完整原型见 [lessons/ch09.py](../lessons/ch09.py)：

```python
def project_prompt(workspace):
    instructions = (workspace / "MINICODE.md").read_text(encoding="utf-8")
    return "你是项目助手。请遵守下面的项目约定：\n" + instructions
```

把返回值传给已有 Agent。这里的 `workspace`、`model`、`runner` 沿用前章对象：

```python
system_prompt = project_prompt(workspace)
agent = Agent(model, runner, system_prompt)
for event in agent.run("这个项目应该怎样运行测试？"):
    print(event)
```

数据流是 `MINICODE.md → 字符串 → system 消息 → 模型请求`。Agent 循环和工具调用都没有变化。

## 运行本章检查点

沿用第 00 章配置的 `MINICODE_MODEL`、`MINICODE_BASE_URL`、`MINICODE_API_KEY`。默认使用真实 API；回答措辞不固定，观察消息与工具的实际变化。需要独立工作目录时，在教程仓库根目录导出：

```bash
uv run python scripts/check_tutorial.py --chapter 9 --export /tmp/minicode-ch09
cd /tmp/minicode-ch09
uv sync
```

在仓库根目录或导出目录运行：

```bash
uv run python examples/09_context.py --step minimal
uv run python examples/09_context.py --step enhanced
```

给同一命令加 `--offline` 可运行固定回复的回归检查。它用来验证程序控制流；学习时先观察真实请求。协议可用 `--api` 选择。


`minimal` 会创建临时项目说明，先打印发送的 system，再请求模型。修改 [示例](../examples/09_context.py) 中的测试命令，重新运行，比较 system 和模型回答。先确认请求里确实带了新信息，再观察模型是否采用它。

## 2. 从已知文件扩展到用户指定的工作区

最小原型跑通后，再把读取提取成 [load_project_instructions()](../minicode/context.py)：

```python
from minicode.context import build_system_prompt, load_project_instructions

instructions = load_project_instructions(workspace)
system_prompt = build_system_prompt(workspace, instructions)
agent = Agent(model, runner, system_prompt)
```

`enhanced` 运行这条路径。增强版本依次补上：文件不存在就返回空说明；解析路径后确认仍在工作区内；说明过长时报错。这些检查解决配置输入的问题，不改变“读取后加入 system”的骨架。

| 遇到的情况 | 增加的处理 |
| --- | --- |
| 新项目没有说明文件 | 用默认系统提示词继续 |
| 用户传入 `../` 或指向外部的符号链接 | 检查解析后的路径 |
| 说明太长 | 要求拆成按需读取的材料 |

## 3. 区分约定和工具读到的资料

应用明确加载的项目说明进入 system；工具读到的代码、网页或 README 留在 `ToolResult`。来源决定程序怎样组织信息。工具结果中的一句话不会重新配置权限，实际操作仍经过第 07 章的执行层。

适配器只转换已经组装好的消息。不要让各个协议适配器分别读取项目说明，否则切换协议就可能改变上下文。

## 检查与练习

```bash
uv run python examples/09_context.py --step minimal --offline
uv run python examples/09_context.py --step enhanced --offline
uv run python -m pytest tests/test_lesson_09.py tests/test_context.py -q
```

先检查请求的 system 是否包含约定；再验证缺失文件、路径越界和长度限制。离线固定回复不能证明模型遵守了约定。

练习：把约定改成“回答分为结论和验证两段”，观察真实模型的输出；然后删去说明文件，用增强版本确认应用仍能启动。

本章新增 [最小原型](../lessons/ch09.py)、[增强实现](../minicode/context.py)、[运行入口](../examples/09_context.py)。下一章保存这些实际发生的对话。
