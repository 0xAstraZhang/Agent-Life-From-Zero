# 第 13 章：Skills，按需加载做事的方法

上一章：[子 Agent](12-subagents.md) · 下一章：[MCP](14-mcp.md)

Agent 已经有执行动作的工具，也能把调查交给子 Agent。
现在遇到另一类重复：代码评审、实验记录整理、发布检查，都有相对固定的步骤。
把这些步骤保存为文档，可以减少每次重新描述任务方法的工作。

本章新增 [skills.py](../minicode/skills.py)，实现显式目录中的技能发现与按需读取。
技能提供方法，工具提供动作，权限仍由执行层判断。

## 先运行示例

```bash
uv run python examples/13_skills.py
```

输出：

```text
- review: 检查改动是否满足验收标准。
已加载评审步骤，现在检查差异。
```

示例中的模型先看到技能简介，再通过 `Skill` 工具读取完整正文。
正文进入普通工具结果，不会直接执行其中的命令。

## 第一步：约定一个足够简单的文件格式

本教程采用一个目录一个技能，每个目录包含 `SKILL.md`：

```text
skills/
  review/
    SKILL.md
  release/
    SKILL.md
```

示例文件内容：

```markdown
# 代码评审

检查改动是否满足验收标准。

## 步骤

1. 读当前差异，确认修改目标。
2. 查调用方和相关测试。
3. 运行受影响的测试。
4. 按问题严重程度报告结果，附文件位置。

## 验收

结论要区分实际复现的问题与尚未验证的疑点。
```

文件夹名是技能名称，第一条非标题的非空行是简介。
这是 minicode 的教学格式，没有实现其他工具使用的全部 YAML frontmatter 字段。
后续要兼容更完整的技能格式，应新增解析器与格式测试。

## 第二步：发现时只读取简介

`SkillLibrary` 接收一个显式路径。
不会自动搜索用户主目录，也不会扫描每个父目录。

```python
library = SkillLibrary(workspace / "skills")
catalog = library.catalog()
```

`catalog()` 对每个 `SKILL.md` 只读取开头 1024 个字符，从中取出简介。
展示的简介最多 160 个字符。

如果把所有正文都塞进系统提示词，即使任务不需要它们，也会付出上下文成本。
简介的作用是帮助模型决定是否需要进一步读取。

技能目录没有找到时，目录列表为空，Agent 仍可使用现有工具完成任务。
技能只是额外的任务方法，不是运行主循环的前置依赖。

## 第三步：把目录交给模型

把目录文本放进应用组装的提示词，同时注册一个 `Skill` 工具。

```python
runner = ToolRunner([*coding_tools, library.tool()], context)
agent = Agent(model, runner, system_prompt + "\n可用技能：\n" + library.catalog())
```

工具参数很简单：

```json
{"name":"review"}
```

模型可以根据简介决定是否调用这个工具。
测试中的 FakeModel 则明确写出调用，从而验证加载链路。

## 第四步：读取正文时检查边界

`load(name)` 只接受一个目录名。
包含 `../`、绝对路径或目录分隔符的名称不能直接变成文件路径。
解析路径后，还要确认符号链接没有跳到技能根目录以外。

```python
path = (library.directory / name / "SKILL.md").resolve()
if not path.is_relative_to(library.directory):
    raise ValueError("技能路径越过了指定目录")
```

实际实现还会先检查名称本身。
缺失文件、编码错误或过长文件都作为工具错误返回，让模型能解释发生了什么。

正文默认最多 20,000 个字符。
超过上限时报错，不悄悄截断步骤。
详细材料可以拆成其他文件，再由用户已经授权的文件工具按需读取。

## 第五步：保留普通工具调用语义

Skill 工具返回完整文档以及来源路径。
内部表示仍然是 `ToolResult`，三个协议适配器按各自格式编码。

```text
assistant → ToolCall(name="Skill", arguments={"name":"review"})
tool      → ToolResult(content="技能 review（来源：...）：...")
assistant → 按步骤检查差异
```

Skill 工具标为只读且可安全并行，因为它只读取已指定目录内的文件。
文档中即使写了 shell 命令，也不会在加载时自动执行。
真正执行命令还需要模型发出 Bash 调用，并经过现有权限检查。

## Skill、Tool 和子 Agent 的区别

| 组件 | 提供什么 | 本教程中的例子 |
| --- | --- | --- |
| Tool | 一个可以执行的动作 | Read、Edit、Bash |
| Skill | 处理一类任务的方法 | 代码评审步骤 |
| 子 Agent | 一段独立运行的调查过程 | 调查测试入口 |
| MCP | 接入外部工具的协议 | 下一章的外部工具 |

它们可以组合：主 Agent 加载评审技能，再委派只读调查，最后使用工具验证结论。
组合时每一层仍保留自己的权限和结果边界。

## 第六步：检查渐进加载是否真的生效

检查第一次请求里只有目录，没有正文细节；
检查工具结果里有完整正文；
检查后续请求能看到这个结果。

```python
model = FakeModel([call("Skill", name="review"), "完成"])
agent = Agent(model, runner, "可用技能：\n" + library.catalog())
list(agent.run("评审修改"))
assert len(model.requests) == 2
```

完整示例位于 [13_skills.py](../examples/13_skills.py)。
它创建临时技能目录，示例结束后自动清理。

## 离线检查

```bash
uv run pytest tests/test_extensions.py -k skill -q
```

测试检查目录只显示简介、正文按需加载、拒绝路径穿越、拒绝符号链接越界、
以及超长正文以错误结果返回。

## 开源实现中的对应边界

[pi 的工具与消息类型](https://github.com/earendil-works/pi/blob/676af1d777ef6f04b6bc8daf4cf3b9b1799cff4d/packages/ai/src/types.ts)
让工具定义和工具结果成为稳定的接口。
minicode 在这个接口上增加 Skill 工具，不需要为“读取方法文档”引入第四种模型协议。
完整技能发现、元数据兼容和资源定位可以继续在 SkillLibrary 中演进。

## 自检与练习

1. 写一个发布检查技能，包含明确的输入、步骤和验收条件。
2. 对同一个任务分别使用技能和不使用技能，比较实际结果，而不只比较回答长度。
3. 增加一项技能格式检查：简介缺失时给出明确错误。
4. 解释为什么技能文档不能直接授权一次原本被拒绝的 Bash 调用。
