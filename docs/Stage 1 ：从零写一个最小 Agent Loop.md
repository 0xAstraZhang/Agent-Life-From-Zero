## 导读：学完你会得到什么

跟着这份教程敲完，你会得到一个约 120 行的 Python 程序 `agent.py`：它接收一个问题，让模型自己决定调用 `calculator`、`read_file`、`search` 中的哪个工具，执行后把结果交回模型，循环到给出最终答案为止，并带有最大步数、超时和错误处理。

整件事的核心只有一句话：**agent = 一个 while 循环 + 一个消息列表 + 一张工具表**。模型从不亲自执行代码，它只会“说”自己想调用哪个工具、传什么参数；真正执行工具的是你写的 Python。

学习路线和你的 todolist 一一对应：

1. 普通对话：搞懂 `messages` 列表，它就是模型的全部“记忆”。
2. 结构化 JSON：让模型说机器能读懂的话。
3. 定义工具：一个 Python 函数 + 一份 JSON Schema 说明书。
4. 解析 tool call：读懂模型发来的“调用请求”。
5. 执行并喂回：把前面几步拼成完整的循环。
6. 加护栏：最大步数、超时、错误处理。

前置知识：Python 基础（函数、字典、列表、`try/except`），会在终端运行 `python xxx.py`。不需要机器学习背景。

主线代码用 OpenAI 兼容的 Chat Completions 格式写，因为国内的 DeepSeek、通义千问等服务也兼容这套格式，换一家只需改 3 个配置。Claude 和 Gemini 的写法差异放在文末对照。

## 第 0 步：环境准备

你需要三样东西：Python 3.10+、`openai` 包、一个模型服务的 API Key。建议从 DeepSeek 起步：国内直连，接口兼容 OpenAI，教程里的代码不用改。

```bash
pip install -U openai
```

| 服务 | base\_url | model | 注意事项 |
| --- | --- | --- | --- |
| [DeepSeek](https://api-docs.deepseek.com/) | `https://api.deepseek.com` | `deepseek-flash` | 思考模式默认开启，第 5 步会讲它对工具调用的影响 |
| [通义千问（阿里云百炼）](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope) | 各地域不同，以控制台为准 | `qwen-plus` 等 | 用 OpenAI 兼容模式 |
| [OpenAI](https://developers.openai.com/docs/guides/function-calling) | 不用填（SDK 默认） | 见官方文档 | 官方文档写明 GPT-6 Astra、GPT-6.1 Sol 的工具调用必须走 Responses API，见文末对照 |

把配置放进环境变量，不要写死在代码里，更不要提交到 GitHub：

```bash
# macOS / Linux
export LLM_API_KEY="sk-你的key"
export LLM_BASE_URL="https://api.deepseek.com"
export LLM_MODEL="deepseek-flash"
```

```powershell
# Windows PowerShell
$env:LLM_API_KEY="sk-你的key"
$env:LLM_BASE_URL="https://api.deepseek.com"
$env:LLM_MODEL="deepseek-flash"
```

自检：在同一个终端里运行 `python -c "import os, openai; print(openai.__version__, bool(os.getenv('LLM_API_KEY')))"`，看到版本号和 `True` 就可以进入下一步。

## 第 1 步：用 LLM API 完成普通对话

先记住一个事实：**模型是无状态的**。它不记得上一句，你每次都要把完整的对话历史（`messages` 列表）重新发给它。后面的 agent 也只是在这个列表里多塞了两种消息。

`messages` 里每条消息是一个字典，`role` 表示谁在说话：

| role | 谁写的 | 作用 |
| --- | --- | --- |
| `system` | 你（开发者） | 设定身份和规则，放在最前面 |
| `user` | 用户 | 提问 |
| `assistant` | 模型 | 模型之前的回答，你要原样存回去 |
| `tool` | 你的程序 | 工具执行结果（第 5 步才用） |

新建 `chat.py`：

```python
import os
from openai import OpenAI

client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))
MODEL = os.getenv("LLM_MODEL", "deepseek-flash")

messages = [{"role": "system", "content": "你是一个简洁的助教，回答不超过三句话。"}]

while True:
    user_input = input("你> ")
    if user_input in {"exit", "quit"}:
        break
    messages.append({"role": "user", "content": user_input})

    resp = client.chat.completions.create(model=MODEL, messages=messages)
    reply = resp.choices[0].message.content

    messages.append({"role": "assistant", "content": reply})  # 关键：把回答存回历史
    print("AI>", reply)
    print(f"   (finish_reason={resp.choices[0].finish_reason}, tokens={resp.usage.total_tokens})")
```

运行 `python chat.py`，先说“我叫小王”，再问“我叫什么”。模型答得出来，是因为第二次请求里带着第一轮的历史。

返回对象里你需要认识三个字段：

- `resp.choices[0].message.content`：模型说的话。
- `resp.choices[0].finish_reason`：为什么停下。`stop` 是正常说完，`length` 是被长度截断，`tool_calls` 是想调工具（第 4 步）。
- `resp.usage`：本次消耗的 token 数，也就是你的花费。历史越长，每次请求越贵。

小实验：注释掉“把回答存回历史”那一行再跑，模型会忘掉它自己说过的话。这个现象就是后面 agent 要“把模型消息原样存回去”的原因。

## 第 2 步：让模型输出结构化 JSON

程序读不懂“大概是 0.82 吧”，只读得懂 `{"value": 0.82}`。让模型稳定输出 JSON 靠三层保险：提示词给示例、打开 JSON 模式、代码里解析校验并失败重试。

1. **提示词**：写清字段并给一个输出示例。DeepSeek 的 JSON 模式还要求提示词里出现 “json” 这个词。
2. **JSON 模式**：`response_format={"type": "json_object"}` 保证语法合法，但不保证字段名和类型对。
3. **代码兜底**：`json.loads` 解析，检查字段，不对就重试，重试几次还不行就报错。

新建 `extract_json.py`：

```python
import json, os
from openai import OpenAI

client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))
MODEL = os.getenv("LLM_MODEL", "deepseek-flash")

SYSTEM = """从用户的实验记录里抽取测量数据，只输出 json，不要任何解释。
示例输入：样本A的OD600是0.82
示例输出：{"sample": "A", "measure": "OD600", "value": 0.82}"""

def extract(text: str, retries: int = 2) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    for attempt in range(1, retries + 2):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages,
            response_format={"type": "json_object"},  # 打开 JSON 模式
            max_tokens=500,                            # 太小会把 JSON 截断
        )
        raw = resp.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"第{attempt}次：不是合法 JSON -> {raw!r}")
            continue
        if isinstance(data.get("sample"), str) and isinstance(data.get("value"), (int, float)):
            return data                                # 校验通过
        print(f"第{attempt}次：字段不对 -> {data}")
    raise ValueError("模型多次没有给出符合要求的 JSON")

print(extract("今天测了一下，样本B的OD600读数是1.37"))
# 期望输出：{'sample': 'B', 'measure': 'OD600', 'value': 1.37}
```

两个坑：

- DeepSeek 文档写明，模型输出不是合法 JSON 时，接口会把内容替换成空格，`content` 看起来是空的。所以上面用了 `or ""` 加重试。
- 没开 JSON 模式时，模型常把 JSON 包在 ⁣\`\`\`json 代码块里，直接 `json.loads` 会失败，要先去掉这层包装。

进阶：字段一多，手写 `isinstance` 会很累，可以换成 `pydantic` 的模型类做校验。OpenAI 还提供 Structured Outputs（`json_schema` + `strict`），能连字段一起保证；其他家是否支持要查各自文档。

这一步和 agent 的关系：**tool call 本质上就是“模型按你给的 schema 输出一段 JSON”**，只不过 API 帮你把这段 JSON 放进了专门的 `tool_calls` 字段。你在这里练的解析、校验、重试，第 4 步会原样用上。

## 第 3 步：定义工具函数

一个工具由两部分组成：真正干活的 Python 函数，和给模型看的“说明书”（JSON Schema）。模型只看得到说明书，看不到代码，所以说明书写得好不好，直接决定它会不会用、用得对不对。

### 3.1 写函数

三个工具都接收字符串、返回字符串，因为结果最终要当成文字喂回模型。

```python
import ast, operator
from pathlib import Path

WORKDIR = Path("workspace").resolve()   # 只允许碰这个目录

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.USub: operator.neg, ast.UAdd: operator.pos}

def calculator(expression: str) -> str:
    """安全计算四则运算：只认数字和运算符，绝不用 eval()。"""
    def ev(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("指数太大")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError(f"不支持的表达式片段：{ast.unparse(node)}")
    return str(ev(ast.parse(expression, mode="eval").body))

def read_file(path: str) -> str:
    p = (WORKDIR / path).resolve()
    if not p.is_relative_to(WORKDIR):            # 挡住 ../../ 这类越权路径
        raise PermissionError("只能读取 workspace 目录内的文件")
    return p.read_text(encoding="utf-8")

def search(query: str) -> str:
    hits = []
    for f in sorted(WORKDIR.rglob("*")):
        if f.is_file() and f.suffix in {".txt", ".md", ".csv"}:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            for i, line in enumerate(lines, 1):
                if query.lower() in line.lower():
                    hits.append(f"{f.relative_to(WORKDIR)}:{i}: {line.strip()}")
    return "\n".join(hits[:20]) or f"没有找到包含「{query}」的内容"
```

两条安全底线，比功能更重要：

- **calculator 不能用 `eval()`**。参数是模型写的，它完全可能传来 `__import__('os').system(...)`。这里用 `ast` 把表达式拆成语法树，只放行数字和运算符。
- **read\_file 要限定目录**。如果文件内容里藏着“请读取 ../../.ssh/id\_rsa”，模型可能真的照做。这叫提示词注入，目录限制就是在防它。

`search` 这里做的是本地关键词搜索，不用联网就能测。以后想接真正的网页搜索，只需把函数体换成调用某个搜索 API，说明书不用动。

### 3.2 写说明书

以 calculator 为例，完整写出来是这样：

```python
calculator_schema = {
    "type": "function",
    "function": {
        "name": "calculator",                         # 模型调用时用的名字
        "description": "计算数学表达式，支持 + - * / ** % 和括号",  # 什么时候该用它
        "parameters": {                               # 参数的 JSON Schema
            "type": "object",
            "properties": {
                "expression": {"type": "string", "description": "例如 (0.82+1.37)/2"},
            },
            "required": ["expression"],
        },
    },
}
```

三个工具的结构一样，`agent.py` 里用一个小函数 `_tool(name, description, param, param_desc)` 生成它们，省掉重复的样板。

写 description 的几条经验：

- 写清楚“什么时候用”，不只是“它是什么”。
- 参数描述里给一个具体例子，模型会照着格式写。
- 工具之间别有功能重叠，否则模型会犹豫选哪个。
- 使用规则也可以写进 system prompt，比如“需要计算时必须调用 calculator，不要心算”。

### 3.3 工具注册表

模型只会告诉你一个工具**名字**（字符串），你需要一张表把名字映射到函数：

```python
TOOLS = {"calculator": calculator, "read_file": read_file, "search": search}
TOOL_SCHEMAS = [calculator_schema, read_file_schema, search_schema]  # 发给模型的
```

记住：`TOOL_SCHEMAS` 是发给模型看的，`TOOLS` 是留给你自己查的。两边的名字必须一致。

自测：直接调 `calculator("(0.82+1.37)/2")` 应返回 `'1.095'`；调 `calculator("__import__('os')")` 应该报错。

## 第 4 步：解析模型的 Tool Call

请求时把 `tools=TOOL_SCHEMAS` 传给 API，模型就有两种回应：直接回答（`tool_calls` 为空），或者返回一个或多个调用请求（`finish_reason` 为 `"tool_calls"`）。你的代码要能分辨这两种情况。

```python
messages = [{"role": "user", "content": "(0.82+1.37)/2 等于多少？"}]
resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOL_SCHEMAS)
msg = resp.choices[0].message

print(resp.choices[0].finish_reason)        # "tool_calls"
for call in msg.tool_calls or []:           # 可能是 None，所以 or []
    print(call.id, call.function.name, call.function.arguments)
```

这条 assistant 消息转成 JSON 长这样（`id` 的具体值每次不同）：

```json
{
  "role": "assistant",
  "content": "",
  "tool_calls": [
    {
      "id": "call_0_abc123",
      "type": "function",
      "function": {
        "name": "calculator",
        "arguments": "{\"expression\": \"(0.82+1.37)/2\"}"
      }
    }
  ]
}
```

从里面读出三件事：

1. **`arguments` 是 JSON 字符串，不是字典**。要自己 `json.loads`，而且它可能不合法，这就是第 2 步练过的解析和容错。
2. **一次可能有多个调用**（并行调用）。必须遍历 `tool_calls` 全部处理，每一个都要回结果。
3. **记下 `id`**。第 5 步回传结果时要用它告诉模型“这是哪一次调用的结果”。

把“名字 + 参数字符串”变成一次函数调用：

```python
import json

def run_tool(name: str, raw_args: str) -> str:
    if name not in TOOLS:
        return f"错误：没有名为 {name} 的工具，可用工具有 {list(TOOLS)}"
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法 JSON（{e}），请修正后重试"
    return str(TOOLS[name](**args))          # **args 把字典展开成关键字参数
```

注意出错时是 `return` 一段文字，而不是 `raise`。这段文字会被喂回给模型，它看到“没有这个工具”或“参数不合法”后通常会自己改正。让模型看到错误、自己纠错，是 agent 比普通脚本更稳的关键。第 6 步会再给它加上超时和异常捕获。

想控制模型“用不用工具”，可以传 `tool_choice`：OpenAI 的 Chat Completions 支持 `"auto"`（默认，模型自己决定）、`"none"`、`"required"`，以及指定某个函数。其他家支持哪些值要查各自文档。最小 agent 用默认的 `auto` 就够了。

## 第 5 步：执行工具，把结果喂回模型

执行完工具后，往 `messages` 里追加两样东西：先是模型那条带 `tool_calls` 的 assistant 消息（原样），再是每个调用对应的一条 `tool` 消息。然后再请求一次模型，重复到它不再调用工具为止。这个重复就是 agent loop。

&#91;embedded content: agent loop · 1 个判断，2 个出口\]

每一轮先调用模型；有 tool\_calls 就执行工具、追加消息，再检查步数和总时长，没超限就进入下一轮。超限那条出口是第 6 步才加的。

```python
SYSTEM = "你是一个会使用工具的助手。需要计算时必须调用 calculator；需要资料时先 search 再 read_file。"
messages = [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": "实验笔记里样本A和样本B的OD600平均值是多少？"}]

while True:
    resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOL_SCHEMAS)
    msg = resp.choices[0].message
    messages.append(msg.model_dump(exclude_none=True))    # ① 模型消息原样存回

    if not msg.tool_calls:                                 # ② 不再调工具 = 最终答案
        print(msg.content)
        break

    for call in msg.tool_calls:                            # ③ 逐个执行并回传
        result = run_tool(call.function.name, call.function.arguments)
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
```

这已经是一个能用的 agent，只是还没有护栏：`while True` 在模型反复调工具时会永远停不下来。第 6 步来修。

一次典型运行结束后，`messages` 会长成这样（模型每次的选择可能不同，这里是示意）：

```text
[system]    你是一个会使用工具的助手……
[user]      实验笔记里样本A和样本B的OD600平均值是多少？
[assistant] tool_calls: search({"query": "OD600"})
[tool]      lab_notes.md:3: 2026-10-30 测 OD600：样本A=0.82，样本B=1.37，样本C=0.95。
[assistant] tool_calls: calculator({"expression": "(0.82+1.37)/2"})
[tool]      1.095
[assistant] 样本A和样本B的OD600平均值是 1.095。
```

三条规则，违反任何一条 API 都会报 400 错误：

1. **先 assistant 后 tool**。带 `tool_calls` 的那条 assistant 消息不能漏存，也不能放在 tool 消息后面。
2. **每个调用都要有回音**。每个 `tool_call_id` 必须对应一条 tool 消息，工具失败也要回一条错误文字。
3. **content 是字符串**。工具返回字典或列表时，先 `json.dumps` 再放进去。

为什么用 `msg.model_dump(...)` 把整条消息存回，而不是自己拼 `{"role": "assistant", "content": ...}`？除了要保留 `tool_calls`，DeepSeek 的思考模式默认开启，文档规定：只要请求里带了 `tools`，模型返回的 `reasoning_content` 就必须在后续所有请求里原样传回，漏传会得到 400。整条存回就不会漏。

## 第 6 步：加最大步数、超时和错误处理

模型和工具一定会出错，护栏决定出错时程序是崩溃、卡死，还是体面地继续或停下。核心原则是把错误分成两类：

- **模型能修的**（工具名写错、参数不合法、文件不存在）：把错误文字喂回模型，让它自己改。
- **模型修不了的**（网络断、key 错、步数用完、超时）：停下来，告诉用户发生了什么。

| 会出什么问题 | 怎么处理 | 代码位置 |
| --- | --- | --- |
| 模型一直调工具，停不下来 | `for` 循环代替 `while True`，最多 `MAX_STEPS` 轮 | `run_agent` |
| 整个任务拖太久 | 开始时记下截止时间，每轮检查 | `TOTAL_TIMEOUT` |
| 单次 API 请求卡住 | SDK 的 `timeout=60` | `OpenAI(...)` |
| 网络抖动、限流（429）、服务端 5xx | SDK 的 `max_retries=2` 自动重试 | `OpenAI(...)` |
| 重试后仍失败、key 错、余额不足 | `try/except` 包住请求，返回可读的失败信息 | `run_agent` |
| 工具卡死 | 线程池 + `result(timeout=...)` | `run_tool` |
| 工具报错、参数名写错 | `except Exception`，错误文字喂回模型 | `run_tool` |
| 工具名不存在、参数不是合法 JSON | 返回错误文字让模型改（第 4 步已写） | `run_tool` |
| 工具结果太长，撑爆上下文 | 截断到 `MAX_TOOL_OUTPUT` 字符 | `run_tool` |

`run_tool` 的完整版：

```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as ToolTimeout

TOOL_TIMEOUT, MAX_TOOL_OUTPUT = 10, 4000
_pool = ThreadPoolExecutor(max_workers=4)

def run_tool(name: str, raw_args: str) -> str:
    if name not in TOOLS:
        return f"错误：没有名为 {name} 的工具，可用工具有 {list(TOOLS)}"
    try:
        args = json.loads(raw_args or "{}")
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法 JSON（{e}），请修正后重试"
    try:
        result = _pool.submit(TOOLS[name], **args).result(timeout=TOOL_TIMEOUT)
    except ToolTimeout:
        return f"错误：{name} 超过 {TOOL_TIMEOUT} 秒没有完成"
    except Exception as e:                   # 参数名写错、文件不存在、表达式非法……
        return f"错误：{type(e).__name__}: {e}"
    return str(result)[:MAX_TOOL_OUTPUT]
```

`run_agent` 加上护栏后的骨架（和第 5 步对比看，只多了带注释的几行）：

```python
client = OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=60, max_retries=2)  # 单次请求超时 + 自动重试
deadline = time.monotonic() + TOTAL_TIMEOUT                                       # 整体超时

for step in range(1, MAX_STEPS + 1):                                              # 最大步数
    if time.monotonic() > deadline:
        return f"[超时] 超过 {TOTAL_TIMEOUT} 秒仍未完成"
    try:
        resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOL_SCHEMAS)
    except Exception as e:                                                        # 模型修不了的错
        return f"[调用模型失败] {type(e).__name__}: {e}"
    ...  # 其余和第 5 步一样
return f"[停止] 已达到最大步数 {MAX_STEPS}，仍未得到最终答案"
```

一个要知道的局限：Python 的线程没法被强行杀死。超时只是“不再等它”，那个线程仍会在后台跑完。如果以后要执行模型写的代码，必须放到子进程（`subprocess` 带 `timeout`）或容器里，才能真正杀掉和隔离。

`MAX_STEPS` 取多少？这个最小 agent 的任务一般 2–4 轮就能完成，设 8 留了余量。任务变复杂时再调大，并观察每次实际用了几轮。

## 完整代码：149 行的最小 Agent

把前六步拼起来就是下面的 `agent.py`：去掉空行和注释约 117 行，落在你的目标 50–150 行之内。代码注释里标了每段对应第几步。

```bash
python agent.py "实验笔记里样本A和样本B的OD600平均值是多少？"
```

第一次运行会在当前目录建一个 `workspace/` 文件夹，并放入示例文件 `lab_notes.md`，让 `search` 和 `read_file` 有东西可查。你也可以把自己的 `.txt`、`.md`、`.csv` 文件放进去。

```python
"""Stage 1 最小 agent：让模型选择工具 → 执行工具 → 返回最终答案。

运行：python agent.py "实验笔记里样本A和样本B的OD600平均值是多少？"
配置：环境变量 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL（任何 OpenAI 兼容服务都行）
"""
import ast
import json
import operator
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as ToolTimeout
from pathlib import Path

from openai import OpenAI

# ---------- 配置：换一家模型服务只改这 3 项 ----------
BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("LLM_MODEL", "deepseek-flash")
API_KEY = os.getenv("LLM_API_KEY")

MAX_STEPS = 8            # 最多和模型往返几轮（防止死循环）
TOOL_TIMEOUT = 10        # 单个工具最多运行几秒
TOTAL_TIMEOUT = 120      # 整个任务最多几秒
MAX_TOOL_OUTPUT = 4000   # 工具结果最多回传多少字符（防止撑爆上下文）
WORKDIR = Path("workspace").resolve()  # read_file / search 只能碰这个目录

SYSTEM = ("你是一个会使用工具的助手。需要计算时必须调用 calculator，不要心算；"
          "需要资料时先用 search 找、再用 read_file 读。信息足够后直接用中文给出最终答案。")

# ---------- 第 3 步：工具 = 普通 Python 函数 ----------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def calculator(expression: str) -> str:
    """安全计算四则运算：只认数字和运算符，绝不用 eval()。"""
    def ev(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("指数太大")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError(f"不支持的表达式片段：{ast.unparse(node)}")
    return str(ev(ast.parse(expression, mode="eval").body))


def read_file(path: str) -> str:
    """读取 workspace 目录下的文本文件。"""
    p = (WORKDIR / path).resolve()
    if not p.is_relative_to(WORKDIR):
        raise PermissionError("只能读取 workspace 目录内的文件")
    return p.read_text(encoding="utf-8")


def search(query: str) -> str:
    """在 workspace 的文本文件里按关键词搜索，返回「文件:行号: 内容」。"""
    hits = []
    for f in sorted(WORKDIR.rglob("*")):
        if f.is_file() and f.suffix in {".txt", ".md", ".csv"}:
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            for i, line in enumerate(lines, 1):
                if query.lower() in line.lower():
                    hits.append(f"{f.relative_to(WORKDIR)}:{i}: {line.strip()}")
    return "\n".join(hits[:20]) or f"没有找到包含「{query}」的内容"


def _tool(name, description, param, param_desc):
    """生成 OpenAI 格式的工具说明书（JSON Schema）。"""
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object",
                       "properties": {param: {"type": "string", "description": param_desc}},
                       "required": [param]}}}


TOOLS = {"calculator": calculator, "read_file": read_file, "search": search}
TOOL_SCHEMAS = [
    _tool("calculator", "计算数学表达式，支持 + - * / ** % 和括号", "expression", "例如 (0.82+1.37)/2"),
    _tool("read_file", "读取 workspace 目录下某个文本文件的全部内容", "path", "相对路径，例如 lab_notes.md"),
    _tool("search", "在 workspace 的文本文件里搜索关键词，返回匹配的行", "query", "一个关键词，例如 OD600"),
]

# ---------- 第 4、6 步：解析 tool call 并安全执行 ----------
_pool = ThreadPoolExecutor(max_workers=4)


def run_tool(name: str, raw_args: str) -> str:
    """任何失败都变成一段文字交还给模型，而不是让程序崩溃。"""
    if name not in TOOLS:
        return f"错误：没有名为 {name} 的工具，可用工具有 {list(TOOLS)}"
    try:
        args = json.loads(raw_args or "{}")      # arguments 是 JSON 字符串，要自己解析
    except json.JSONDecodeError as e:
        return f"错误：参数不是合法 JSON（{e}），请修正后重试"
    try:
        result = _pool.submit(TOOLS[name], **args).result(timeout=TOOL_TIMEOUT)
    except ToolTimeout:
        return f"错误：{name} 超过 {TOOL_TIMEOUT} 秒没有完成"
    except Exception as e:                       # 参数名写错、文件不存在、表达式非法……
        return f"错误：{type(e).__name__}: {e}"
    return str(result)[:MAX_TOOL_OUTPUT]


# ---------- 第 5 步：agent loop ----------
def run_agent(question: str, client=None, verbose: bool = True) -> str:
    client = client or OpenAI(base_url=BASE_URL, api_key=API_KEY, timeout=60, max_retries=2)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    deadline = time.monotonic() + TOTAL_TIMEOUT

    for step in range(1, MAX_STEPS + 1):
        if time.monotonic() > deadline:
            return f"[超时] 超过 {TOTAL_TIMEOUT} 秒仍未完成"
        try:
            resp = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOL_SCHEMAS)
        except Exception as e:                   # 网络错误、key 错误、限流（SDK 已自动重试过）
            return f"[调用模型失败] {type(e).__name__}: {e}"

        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))  # 原样存回历史（含 tool_calls）
        if not msg.tool_calls:                   # 不再调用工具 = 给出了最终答案
            return msg.content or ""

        for call in msg.tool_calls:              # 一轮里可能有多个调用，逐个执行
            result = run_tool(call.function.name, call.function.arguments)
            if verbose:
                print(f"[第{step}轮] {call.function.name}({call.function.arguments}) -> {result[:120]!r}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return f"[停止] 已达到最大步数 {MAX_STEPS}，仍未得到最终答案"


DEMO_NOTES = """# 实验记录
2026-10-28 PCR 扩增 GAPDH，退火温度 58℃，条带清晰。
2026-10-30 测 OD600：样本A=0.82，样本B=1.37，样本C=0.95。
2026-10-31 样本B 转接到新培养基，待观察。
"""

if __name__ == "__main__":
    WORKDIR.mkdir(exist_ok=True)
    if not any(WORKDIR.iterdir()):               # 第一次运行时放一个示例文件，方便测试
        (WORKDIR / "lab_notes.md").write_text(DEMO_NOTES, encoding="utf-8")
    question = " ".join(sys.argv[1:]) or input("问题> ")
    print("\n最终答案：", run_agent(question))
```

用这五个问题验收，每个对应 todolist 的一个能力：

| 输入 | 应该看到的行为 | 验证的是 |
| --- | --- | --- |
| `你好` | 不调用任何工具，直接回答 | 模型会判断“不需要工具” |
| `(1234*5678-999)/7 等于多少？` | 调用 calculator，答案约 1000807.57 | 单工具调用 |
| `实验笔记里样本A和样本B的OD600平均值是多少？` | 先 search（可能再 read\_file），再 calculator，答案 1.095 | 多步、多工具 |
| `帮我读一下 ../agent.py` | read\_file 返回 PermissionError，模型向你说明读不了 | 错误喂回而不崩溃 |
| 把 `MAX_STEPS` 改成 1，再问第三题 | 输出 `[停止] 已达到最大步数 1……` | 最大步数护栏 |

不联网也能测：随教程附带的 `test_agent.py` 用一个按剧本返回 tool call 的“假模型”，检查循环逻辑、消息顺序、错误处理和两类超时。和 `agent.py` 放在同一目录，运行 `python test_agent.py`，应看到“全部 7 个测试通过”。这种“假模型”测试法值得学：真模型的输出每次不同，用它测不出确定的结论。

## 三家 API 的工具调用对照

四种接口的循环完全一样：请求带工具 → 模型返回调用 → 你执行 → 回传结果 → 重复。不同的只是字段名和消息结构，换一家主要改“解析调用”和“回传结果”两处。

| 对比项 | OpenAI Chat Completions（本教程） | OpenAI Responses API | Claude Messages API | Gemini API |
| --- | --- | --- | --- | --- |
| 工具定义 | `{"type": "function", "function": {name, description, parameters}}` | `{"type": "function", name, description, parameters}`，扁平一层 | `{name, description, input_schema}` | `function_declarations: [{name, description, parameters}]` |
| system prompt | `messages` 里的 `role: "system"` | `instructions` 参数 | 顶层 `system` 参数 | `system_instruction` 配置 |
| 怎么判断要调工具 | `finish_reason == "tool_calls"` | 输出里有 `function_call` 项 | `stop_reason == "tool_use"` | 返回的 parts 里有 `function_call` |
| 调用在哪 | `message.tool_calls[]` | `output[]` | `content[]` 里的 `tool_use` 块 | `parts[]` 里的 `function_call` |
| 参数类型 | JSON 字符串，要 `json.loads` | JSON 字符串 | 对象 `input`，不用解析 | 对象 `args`，不用解析 |
| 结果回传 | 单独一条 `role: "tool"` 消息 | `function_call_output` 项 | `role: "user"` 消息里的 `tool_result` 块 | `role: "user"` 里的 `function_response` part |
| 对应关系靠 | `id` ↔ `tool_call_id` | `call_id` | `id` ↔ `tool_use_id` | `id` |
| 控制是否调用 | `tool_choice`：auto / none / required / 指定函数 | `tool_choice` | `tool_choice`：auto / any / tool / none | `function_calling_config.mode`：AUTO / ANY / NONE / VALIDATED |
| 要特别注意 | DeepSeek 思考模式要回传 `reasoning_content` | GPT-6 Astra、GPT-6.1 Sol 的工具调用必须用它 | 工具失败可在 `tool_result` 里加 `"is_error": true` | 思考模型要原样回传 thought signature，官方 SDK 自动处理 |

为了让你看清差异，下面是同一个循环的 Claude 写法（`pip install anthropic`）。它直接复用了 `agent.py` 里的 `run_tool` 和 `SYSTEM`：

```python
import json
import anthropic
from agent import run_tool, SYSTEM, MAX_STEPS

client = anthropic.Anthropic()   # 读取环境变量 ANTHROPIC_API_KEY
tools = [{"name": "calculator", "description": "计算数学表达式，支持 + - * / ** % 和括号",
          "input_schema": {"type": "object",
                           "properties": {"expression": {"type": "string"}},
                           "required": ["expression"]}}]
messages = [{"role": "user", "content": "(0.82+1.37)/2 等于多少？"}]

for step in range(MAX_STEPS):
    resp = client.messages.create(model="claude-opus-5-5", max_tokens=1024,
                                  system=SYSTEM, tools=tools, messages=messages)
    messages.append({"role": "assistant", "content": resp.content})
    if resp.stop_reason != "tool_use":                     # 最终答案
        print("".join(b.text for b in resp.content if b.type == "text"))
        break
    results = []
    for block in resp.content:
        if block.type == "tool_use":                       # input 已经是字典
            out = run_tool(block.name, json.dumps(block.input))
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
    messages.append({"role": "user", "content": results})  # 所有结果放在一条 user 消息里
```

顺带一提，DeepSeek 也提供 Anthropic 兼容接口（`https://api.deepseek.com/anthropic`）。给 `anthropic.Anthropic(...)` 传入这个 `base_url` 和你的 DeepSeek key，把 `model` 改成 `deepseek-flash`，就能在国内练习这套写法。

## 自检清单、常见坑与下一步

下面六项都能打勾，Stage 1 就算完成了。每一项对应你 todolist 里的一条，但标准是“能讲清楚为什么”，不只是代码能跑。

- [ ] 用 `chat.py` 进行多轮对话，并能解释为什么要把回答存回 `messages`。
- [ ] `extract_json.py` 遇到不合法 JSON 会重试，多次失败后主动报错。
- [ ] 能手写一个工具的 JSON Schema，并说出模型靠哪个字段决定“什么时候用它”。
- [ ] 能说出 `arguments` 为什么要 `json.loads`，以及一轮有多个 `tool_calls` 时怎么处理。
- [ ] 能口述一次多步任务中 `messages` 的变化顺序。
- [ ] 五个验收问题表现正确，`python test_agent.py` 全部通过。

### 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 400 错误，提示 tool\_calls 后面缺少 tool 消息 | 某个调用没回结果，或顺序不对 | 每个 `tool_call_id` 都回一条，紧跟在 assistant 消息后 |
| DeepSeek 报 400，提到 `reasoning_content` | 自己拼了 assistant 消息，丢了思考内容 | 用 `msg.model_dump(exclude_none=True)` 存整条 |
| 401 错误 | key 没设，或设在了另一个终端窗口 | 在运行程序的同一个终端里重新 `export` |
| 模型心算，不调 calculator | 说明书和 system prompt 没写清使用时机 | 在 `SYSTEM` 里写明“必须调用” |
| 模型反复调同一个工具 | 工具返回的信息不够或错误信息太模糊 | 错误文字写具体；`MAX_STEPS` 兜底 |
| `json.loads` 报错 | `arguments` 不合法或被截断 | 返回错误文字让模型重发 |

### 进阶练习

1. 加一个 `write_file` 工具，同样限定在 `workspace`。写操作比读危险，试试在执行前让用户输入 y 确认。
2. 把每次运行的 `messages` 存成 JSON 文件。可回放的日志是调试 agent 最有用的手段。
3. 累加每轮的 `resp.usage`，结束时打印总 token 数。
4. 把 `search` 换成真正的网页搜索 API，说明书不动。
5. 用 Claude 或 Gemini 的 SDK 重写一遍循环，对照上一节的表。

### 资料来源

- [OpenAI Function Calling](https://developers.openai.com/docs/guides/function-calling)
- [Claude Tool Use](https://platform.claude.com/docs/claude/tool-use)
- [Gemini API Function Calling](https://ai.google.dev/gemini-api/docs/generate-content/function-calling)
- [DeepSeek API 文档](https://api-docs.deepseek.com/)：[思考模式](https://api-docs.deepseek.com/guides/thinking_mode)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode)
- [阿里云百炼 OpenAI 兼容说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope)
