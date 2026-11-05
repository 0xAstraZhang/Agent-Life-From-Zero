# 第 13 章：先看技能简介，需要时再读完整步骤

[上一章：子 Agent](12-subagents.md) · [目录](README.md) · [下一章：MCP](14-mcp.md)

重复任务往往有固定的方法，例如代码评审先推演输入，再检查结果。把方法写在文件里，模型先看简介，决定使用时再读取正文。这就是本章要实现的渐进加载。

## 1. 准备一份能马上使用的技能

创建 `skills/review/SKILL.md`：

```markdown
# 代码评审
检查代码是否符合输入输出约定。

## 步骤
1. 用一组具体输入推演代码。
2. 对照预期结果。
3. 给出错误原因和最小修复。
```

目录名 `review` 是技能名称，第一条非标题的非空行是简介，完整文件是正文。先用这个小格式实现，不需要先实现其他工具的全部元数据格式。

## 2. 最小目录：发现简介，按名称读正文

[lessons/ch13.py](../lessons/ch13.py) 先处理示例自己创建的固定练习目录：

```python
class LessonSkills:
    def __init__(self, directory):
        self.paths = {path.parent.name: path for path in sorted(directory.glob("*/SKILL.md"))}

    def catalog(self):
        entries = []
        for name, path in self.paths.items():
            with path.open(encoding="utf-8") as source:
                for line in source:
                    description = line.strip()
                    if description and not description.startswith("#"):
                        entries.append(f"- {name}: {description}")
                        break
        return "\n".join(entries)

    def load(self, name):
        return self.paths[name].read_text(encoding="utf-8")
```

`catalog()` 找到简介就停止读这个文件。`load()` 才读完整正文。第一次请求只附上 catalog，避免把每个技能的完整内容都放入上下文。

## 3. 把读取正文变成普通工具

```python
def skill_tool(library):
    spec = ToolSpec("Skill", "按名称读取技能的完整步骤。", {
        "type": "object", "properties": {"name": {"type": "string"}},
        "required": ["name"], "additionalProperties": False,
    })

    def execute(args, context):
        return library.load(args["name"])

    return Tool(spec, execute, read_only=True)
```

`Tool`、`ToolSpec` 的导入在原型文件开头。工具函数只把正文返回给模型；模型在下一轮结合正文继续任务：

```text
首次请求：用户任务 + 技能简介
assistant：Skill(name="review")
tool：完整评审步骤
assistant：按步骤推演代码并给出结论
```

这是已有 Agent Loop 的一次工具往返，不需要新的协议、循环或消息角色。

## 4. 运行真实模型，观察正文何时出现

沿用第 00 章的模型环境变量。可先导出本章独立工作目录：

```bash
uv run python scripts/check_tutorial.py --chapter 13 --export /tmp/minicode-ch13
cd /tmp/minicode-ch13
uv sync
```

在仓库根目录或导出目录运行：

```bash
uv run python examples/13_skills.py --step minimal
```

[示例](../examples/13_skills.py) 要求模型使用 review 技能，评审 `def add(a, b): return a - b`，并给出输入 `2、3`、预期输出 `5`。它先打印实际附加的简介，随后打印 Skill 工具返回的正文和模型结论。

修改技能步骤后再次运行，比较模型推演过程。回答措辞不固定；先确认工具结果里有新的步骤，再看结论是否依据这些步骤得出。

## 5. 跑通后再接入真实目录与已有执行层

固定练习目录的原型跑通后，替换为 [SkillLibrary](../minicode/skills.py)。它增加名称检查、解析路径后的工作区检查、符号链接检查、简介和正文长度上限，以及读取失败的工具结果。

接回已有 Agent 时，只扩充内层工具列表，继续使用原来的工具对象、权限配置和 hooks：

```python
library = SkillLibrary(workspace / "skills")
tools.append(library.tool())
runner = assemble(tools)
system_prompt += "\n可用技能：\n" + library.catalog()
```

这里的 `assemble()` 沿用第 11 章的 `HookRunner → PermissionRunner → ToolRunner` 组合。新增 Skill 工具不会改变旧工具的授权，也不会清除读取状态或日志回调。

```bash
uv run python examples/13_skills.py --step enhanced
```

`enhanced` 先验证一次未授权 Write，再加入 Skill 运行真实评审，最后再次验证 Write。两次写入都应被拒绝，hooks 继续记录执行。这组集成回归放在渐进加载之后学习。

技能正文是做事的方法，加载它本身不运行脚本。正文要求执行命令时，仍需要模型另发工具调用，并经过现有执行层。

## 6. 分清三种组件

| 组件 | 提供什么 | 本教程的例子 |
| --- | --- | --- |
| Tool | 一个动作 | 读文件、改文件 |
| Skill | 一类任务的方法 | 评审步骤 |
| 子 Agent | 一段独立调查过程 | 查找测试入口 |

它们可以组合：先加载评审方法，再委派一段独立调查，最后调用工具验证结论。

## 检查与练习

```bash
uv run python examples/13_skills.py --step minimal --offline
uv run python examples/13_skills.py --step enhanced --offline
uv run python -m pytest tests/test_lesson_13.py tests/test_skills.py -q
```

先检查第一次请求只有简介，Skill 工具结果包含正文，下一次请求能看到它；再检查越界、长度和新增工具后的权限回归。`--offline` 只用于确定性检查。

练习：增加一个“检查边界输入”的技能，对同一函数分别使用两份技能，比较实际发现的问题。
