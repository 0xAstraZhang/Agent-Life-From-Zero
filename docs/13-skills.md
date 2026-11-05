# 第 13 章：先看技能简介，需要时再读完整步骤

[上一章：子 Agent](12-subagents.md) · [目录](README.md) · [下一章：MCP](14-mcp.md)

重复任务常有固定的方法，例如代码评审先推演输入，再检查结果。把方法写成文档，模型先看到简介，决定使用时再读取正文。本章亲手实现这个渐进加载过程。

继续使用自己的 `model.py` 和 `agent.py`。只有增强检查点复用第 07 章的权限执行器和第 11 章的 hook。

## 1. 先定义最小目录和读取动作

约定一个技能一个目录，目录里有 `SKILL.md`。目录名是技能名称，第一条非标题的非空行是简介，完整文件是正文。

新建 `skills.py`，写入完整定义：

```python file=skills.py step=minimal
class SkillLibrary:
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

    def tool(self):
        return {
            "spec": {"type": "function", "function": {
                "name": "Skill", "description": "按名称读取技能的完整步骤。",
                "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                               "required": ["name"], "additionalProperties": False},
            }},
            "execute": self.load, "read_only": True,
        }
```


先看两个方法的区别：catalog 找到简介就停止读取；load 才读取整个文件。tool 把 load 包装成前面学过的工具注册记录，执行函数仍在本地。

## 2. 让真实模型选择并读取正文

下面完整的 `demo.py` 创建一份小技能，并要求模型用它评审一个加法函数：

```python file=demo.py step=minimal
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from skills import SkillLibrary


with TemporaryDirectory() as directory:
    root = Path(directory)
    folder = root / "skills" / "review"
    folder.mkdir(parents=True)
    body = "# 代码评审\n检查代码是否符合输入输出约定。\n\n步骤：先用具体输入推演，再对照预期结果。"
    (folder / "SKILL.md").write_text(body, encoding="utf-8")
    library = SkillLibrary(root / "skills")
    catalog = library.catalog()
    print("首次请求只附简介：\n" + catalog)
    tools = {"Skill": library.tool()}
    with open_model() as model:
        agent = Agent(model, tools, system="你是评审助手。可用技能：\n" + catalog)
        print(agent.run("必须先读取 review 技能。用它评审 def add(a, b): return a - b；输入2、3，预期5。"))
    loaded = [m["content"] for m in agent.history if m["role"] == "tool"]
    print("技能正文已进入历史：", body in loaded)
    if body not in loaded:
        raise RuntimeError("没有通过 Skill 读取完整正文")
```


```bash
uv run python demo.py
```

观察三个阶段：首次请求只有简介；模型调用 Skill；下一次请求包含完整正文，模型再应用这些步骤。最小版只处理练习程序自己创建的目录，先理解信息什么时候进入上下文。

修改 body 中的步骤，例如要求列出三个边界输入，再运行，比较真实模型怎样处理同一段代码。

## 3. 再把目录扩展到用户提供的材料

将 `skills.py` 替换为完整增强版，加入名称、路径和长度处理：

```python file=skills.py step=enhanced
from pathlib import Path


class SkillLibrary:
    def __init__(self, directory, max_chars=20_000):
        self.directory = Path(directory).resolve()
        self.max_chars = max_chars

    def path(self, name):
        if not name or Path(name).name != name or name in {".", ".."}:
            raise ValueError("技能名称只能是目录名")
        path = (self.directory / name / "SKILL.md").resolve()
        if not path.is_relative_to(self.directory):
            raise ValueError("技能路径越过指定目录")
        return path

    def catalog(self):
        entries = []
        if not self.directory.exists():
            return ""
        for folder in sorted(self.directory.iterdir()):
            try:
                with self.path(folder.name).open(encoding="utf-8") as source:
                    preview = source.read(1024)
                description = next((line.strip() for line in preview.splitlines()
                                    if line.strip() and not line.lstrip().startswith("#")), "无简介")
                entries.append(f"- {folder.name}: {description[:160]}")
            except (ValueError, OSError):
                continue
        return "\n".join(entries)

    def load(self, name):
        with self.path(name).open(encoding="utf-8") as source:
            text = source.read(self.max_chars + 1)
        if len(text) > self.max_chars:
            raise ValueError("技能正文太长，请拆分材料")
        return text

    def tool(self):
        return {
            "spec": {"type": "function", "function": {
                "name": "Skill", "description": "按名称读取技能的完整步骤。",
                "parameters": {"type": "object", "properties": {"name": {"type": "string"}},
                               "required": ["name"], "additionalProperties": False},
            }},
            "execute": self.load, "read_only": True,
        }
```


路径解析后仍要在指定目录内；正文过长就报错，避免悄悄删掉步骤。工具执行器会把读取错误交还模型，主循环无需增加 Skill 专用分支。

## 4. 最后验收新增技能没有重置已有执行层

新增工具时，保留之前的权限执行器和 hook，只扩充它们使用的注册表。下面完整 `demo.py` 先检查一次写入拒绝，加入 Skill 完成真实任务，再检查一次写入拒绝：

```python file=demo.py step=enhanced
from pathlib import Path
from tempfile import TemporaryDirectory

from model import open_model
from agent import Agent
from skills import SkillLibrary
from permissions import permission_runner
from hooks import hook_runner


with TemporaryDirectory() as directory:
    root = Path(directory)
    folder = root / "skills" / "review"
    folder.mkdir(parents=True)
    body = "# 代码评审\n检查代码是否符合输入输出约定。\n\n步骤：先用具体输入推演，再对照预期结果。"
    (folder / "SKILL.md").write_text(body, encoding="utf-8")
    library = SkillLibrary(root / "skills")
    report = root / "report.txt"

    def write_note():
        report.write_text("报告", encoding="utf-8")
        return "报告已写入"

    tools = {"WriteNote": {
        "spec": {"type": "function", "function": {
            "name": "WriteNote", "description": "写入报告。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        }},
        "execute": write_note, "read_only": False,
    }}
    records = []

    def after(call, result):
        records.append(call["function"]["name"])

    permissions = permission_runner(tools, ask=lambda *_: False)
    runner = hook_runner(permissions, after=after)
    write_call = {"id": "probe", "type": "function", "function": {"name": "WriteNote", "arguments": "{}"}}
    print("加入技能前：", runner(write_call))

    tools["Skill"] = library.tool()
    with open_model() as model:
        agent = Agent(model, tools, runner=runner,
                      system="你是评审助手。可用技能：\n" + library.catalog())
        print(agent.run("必须先读取 review 技能。评审 def add(a, b): return a - b；输入2、3，预期5。"))
    print("加入技能后：", runner(write_call))
    loaded = [m["content"] for m in agent.history if m["role"] == "tool"]
    if body not in loaded or report.exists() or records.count("WriteNote") < 2 or "Skill" not in records:
        raise RuntimeError("技能加载或已有权限、日志未保持")
    print("技能正文已进入历史：", body in loaded)
    print("写入仍被拒绝，日志仍记录 Skill：", "Skill" in records)
```


```bash
uv run python demo.py
```

这个程序使用的 permission_runner 是第 07 章手写的函数，hook_runner 是第 11 章增强步骤手写的函数。注册表保留同一个字典，因此已有包装继续处理新增工具。

技能正文是方法，加载正文不会执行脚本。真正运行命令仍需另一次工具调用，并经过原来的权限检查。

| 组件 | 提供什么 |
| --- | --- |
| Tool | 一个可执行动作 |
| Skill | 一类任务的方法 |
| 子 Agent | 一段独立运行的调查 |

练习：创建第二份技能，让模型根据简介选择；检查第一次请求没有包含两份正文。随后尝试 `../` 名称，验证增强版会返回错误。

章末对照：[完整 Skills 实现](../minicode/skills.py)。本章没有导入仓库实现；目录发现、正文读取、工具声明和执行层组合都已在自己的文件中写出。
