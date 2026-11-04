## 第 1 章：Agent Loop，一切从一个循环开始

### 导读

这一章写出 minicode 的第一版：一个约 90 行的 `agent.py`。它接收一个问题，让模型自己决定要不要调用 `list_files`、`read_file`，执行后把结果交回模型，一直循环到模型给出最终回答，并且能在出错和轮数用完时体面地停下。

整件事只有一句话：**agent = 一个循环 + 一个消息列表 + 一张工具表**。模型从不亲自执行任何东西，它只会说“我想调用哪个工具、传什么参数”；真正执行的是你的代码。

学完你会有：

- `minicode/llm.py`：模型客户端，以后换模型、换假模型都只动这里。
- `minicode/testing.py`：按剧本回复的假模型，之后每一章的离线测试都用它。
- `minicode/agent.py`（第一版）：最小的 agent loop。
- 一组离线测试，验证循环的每一条退出路径。

前置知识：Python 基础。你写过的 `chatbot.py`（多轮对话）和 `extract_json.py`（结构化输出）正好是这一章的起点。

### Claude Code 是怎么做的

Claude Code 官方文档把它的工作方式描述成三个阶段：**收集上下文（gather context）、采取行动（take action）、验证结果（verify results）**，并且说这三个阶段 “blend together”：模型读一点、改一点、跑一下测试，再根据结果决定下一步，可以一口气串起几十个动作。

落到代码上，这就是一个循环。Claude Agent SDK 的文档把一次循环讲得更具体：

1. 收到用户的输入；
2. 模型给出回复，回复里可能包含工具调用；
3. SDK 执行这些工具，把结果交回模型；
4. 重复 2 和 3；
5. 模型的回复里不再有工具调用时，返回结果。

文档把 “模型输出一次 + 执行它要的工具” 叫作一个 **turn（轮）**。循环会一直进行到 “Claude produces output with no tool calls” 为止。

循环结束时，SDK 会给出一条 **result 消息**，里面有最终文本、用量、费用、轮数，以及一个说明“为什么结束”的 `subtype`：

| subtype | 含义 |
| --- | --- |
| `success` | 正常完成 |
| `error_max_turns` | 用完了最大轮数 |
| `error_max_budget_usd` | 花费到了上限 |
| `error_during_execution` | 执行中出错，比如请求被取消 |

minicode 照搬这个设计：`query()` 是一个生成器，边跑边产出事件（`assistant`、`tool_result`），最后一定以一个 `result` 事件结束。这样命令行界面、非交互模式、测试和评测都消费同一串事件，彼此不用知道对方的存在。

### 从你写过的 chatbot.py 说起

回顾两个已经知道的事实，它们是理解 agent loop 的全部前提：

**模型是无状态的。** `chatbot.py` 每一轮都把完整的 `messages` 列表发给模型，模型才“记得”之前说过什么。agent 也一样，只是消息列表里多了两种东西：模型发出的工具调用，和你回传的工具结果。

**工具调用就是“按 schema 输出的 JSON”。** `extract_json.py` 里你让模型按格式输出 JSON 再自己解析。工具调用是同一件事，只不过服务商把这段 JSON 放进了专门的 `tool_calls` 字段，并且保证结构合法（参数内容仍然可能不对）。

一次带工具的对话，消息列表会长成这样：

```text
[system]    你是一个帮用户查看本地文件的助手……
[user]      chatbot.py 是做什么的？
[assistant] tool_calls: read_file({"path": "chatbot.py"})
[tool]      import os ... （文件内容）
[assistant] 它是一个多轮对话脚本……
```

OpenAI 兼容格式有三条硬规则，违反任何一条都会得到 400 错误：

1. 带 `tool_calls` 的 assistant 消息必须原样存回，并且排在对应的 tool 消息前面；
2. 每个 `tool_call_id` 都必须有一条 tool 消息回应，工具失败也要回一条；
3. tool 消息的 `content` 是字符串。

### 第 1 步：模型客户端

新建 `minicode/__init__.py`：

```python
"""minicode：照着 Claude Code 的架构，用 Python 从零写的迷你 coding agent。"""
```

新建 `minicode/llm.py`：

```python
"""模型客户端：把 OpenAI 兼容接口包成一个 complete() 方法。

agent 的其余部分只依赖 complete() 的返回值 Reply，所以换模型服务、
或者在测试里换成假模型，都不用改主循环。
"""
import os
from dataclasses import dataclass

from openai import OpenAI


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Reply:
    message: dict        # assistant 消息，原样存回 messages（保留 tool_calls 和服务商的额外字段）
    usage: Usage
    finish_reason: str   # stop / tool_calls / length ...


class LLM:
    def __init__(self, model: str | None = None, base_url: str | None = None,
                 api_key: str | None = None, timeout: float = 120):
        self.model = model or os.getenv("LLM_MODEL", "deepseek-flash")
        self.client = OpenAI(base_url=base_url or os.getenv("LLM_BASE_URL"),
                             api_key=api_key or os.getenv("LLM_API_KEY"),
                             timeout=timeout, max_retries=2)   # 网络抖动、429、5xx 由 SDK 自动重试

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        kwargs = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        u = resp.usage
        return Reply(message=choice.message.model_dump(exclude_none=True),
                     usage=Usage(u.prompt_tokens, u.completion_tokens) if u else Usage(),
                     finish_reason=choice.finish_reason or "")
```

三个值得注意的地方：

- **`Reply` 是 agent 和模型之间唯一的接口。** 主循环只认 `reply.message`、`reply.usage`、`reply.finish_reason`。下一步的假模型返回同样的结构，主循环分不出真假。
- **`model_dump(exclude_none=True)` 把整条消息原样存下来。** 不要自己拼 `{"role": "assistant", "content": ...}`：那样会丢掉 `tool_calls`，也会丢掉服务商的额外字段。比如 DeepSeek 的思考模式要求带工具的请求把 `reasoning_content` 原样传回，漏了就是 400。
- **`max_retries=2`**：网络抖动、限流（429）、服务端 5xx 交给 SDK 自动重试。重试后仍然失败，才轮到主循环处理。

### 第 2 步：假模型

真模型每次的回答都不一样，测不出确定的结论，还要花钱。所以从第一章开始就准备一个“按剧本回答”的假模型。

新建 `minicode/testing.py`：

```python
"""按剧本回复的假模型：不联网、不花钱、每次结果都一样，专门用来测 agent 的逻辑。

剧本里每一项代表模型的一次回复：
  - 字符串：最终回答（不调用工具）
  - call(...)：调用一个工具；[call(...), call(...)]：一次调用多个工具
  - Exception 实例：模拟网络错误、限流
"""
import copy
import json

from .llm import Reply, Usage


def call(name: str, /, **args) -> dict:
    return {"name": name, "args": args}


class FakeLLM:
    model = "fake-model"

    def __init__(self, script: list):
        self.script = list(script)
        self.requests: list[dict] = []    # 每次请求的副本，测试里可以检查模型“看到”了什么
        self._n = 0

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        self.requests.append(copy.deepcopy({"messages": messages, "tools": tools}))
        step = self.script.pop(0) if self.script else "（剧本已用完）"
        if isinstance(step, Exception):
            raise step
        calls = [step] if isinstance(step, dict) else step if isinstance(step, list) else []
        message = {"role": "assistant", "content": step if isinstance(step, str) else ""}
        if calls:
            message["tool_calls"] = []
            for c in calls:
                self._n += 1
                message["tool_calls"].append({
                    "id": f"call_{self._n}", "type": "function",
                    "function": {"name": c["name"], "arguments": json.dumps(c["args"], ensure_ascii=False)}})
        size = len(json.dumps(messages, ensure_ascii=False))
        return Reply(message, Usage(input_tokens=size // 3, output_tokens=20),
                     "tool_calls" if calls else "stop")
```

剧本的写法：`"字符串"` 是最终回答，`call("工具名", 参数=值)` 是一次工具调用，放进列表就是一次调用多个工具，放一个异常对象就是模拟出错。`requests` 记下了每一次请求的内容，测试里可以检查“模型在第二轮看到了什么”。

### 第 3 步：最小的 Agent Loop

新建 `minicode/agent.py`：

```python
"""第 1 章：最小的 agent loop。第 2 章会把它拆成“工具系统 + 主循环”两部分。

运行：uv run python -m minicode.agent "这个目录里有哪些 Python 文件？"
"""
import json
import sys
import time
from pathlib import Path

from .llm import LLM


# ---------- 工具：普通的 Python 函数 + 给模型看的说明书 ----------
def list_files(directory: str = ".") -> str:
    return "\n".join(sorted(p.name + ("/" if p.is_dir() else "") for p in Path(directory).iterdir()))


def read_file(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


TOOLS = {"list_files": list_files, "read_file": read_file}
SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_files", "description": "列出一个目录里的文件和子目录（子目录以 / 结尾）",
        "parameters": {"type": "object", "properties": {
            "directory": {"type": "string", "description": "目录路径，默认当前目录"}}}}},
    {"type": "function", "function": {
        "name": "read_file", "description": "读取一个文本文件的全部内容",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "文件路径，例如 README.md"}},
            "required": ["path"]}}},
]


class Agent:
    def __init__(self, llm, system_prompt: str, max_turns: int = 10):
        self.llm, self.max_turns = llm, max_turns
        self.messages = [{"role": "system", "content": system_prompt}]

    def query(self, prompt: str):
        """处理一条用户输入。生成器：边跑边产出事件，最后一个事件的 type 是 "result"。"""
        started = time.monotonic()
        self.messages.append({"role": "user", "content": prompt})
        for turn in range(1, self.max_turns + 1):
            try:
                reply = self.llm.complete(self.messages, SCHEMAS)
            except Exception as e:                       # SDK 已经重试过，到这里就是修不好的错
                yield self._result("error_during_execution", f"调用模型失败：{e}", turn, started)
                return
            msg = reply.message
            self.messages.append(msg)                    # 原样存回，包括 tool_calls
            calls = msg.get("tool_calls") or []
            yield {"type": "assistant", "text": msg.get("content") or "",
                   "tool_calls": [{"name": c["function"]["name"], "arguments": c["function"]["arguments"]}
                                  for c in calls]}
            if not calls:                                # 不再调用工具 = 给出了最终回答
                yield self._result("success", msg.get("content") or "", turn, started)
                return
            for call in calls:                           # 每个调用都必须回一条 tool 消息
                output = self.run_tool(call)
                self.messages.append({"role": "tool", "tool_call_id": call["id"], "content": output})
                yield {"type": "tool_result", "name": call["function"]["name"], "content": output}
        yield self._result("error_max_turns", f"已达到最大轮数 {self.max_turns}", self.max_turns, started)

    def run_tool(self, call: dict) -> str:
        """任何失败都变成一段文字交还给模型，而不是让程序崩溃。"""
        name = call["function"]["name"]
        if name not in TOOLS:
            return f"错误：没有名为 {name} 的工具，可用的有 {list(TOOLS)}"
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError as e:
            return f"错误：参数不是合法的 JSON（{e}），请修正后重试"
        try:
            return str(TOOLS[name](**args))[:20_000]     # 太长的结果截断，免得撑爆上下文
        except Exception as e:
            return f"错误：{type(e).__name__}: {e}"

    def _result(self, subtype: str, text: str, turns: int, started: float) -> dict:
        return {"type": "result", "subtype": subtype, "text": text, "num_turns": turns,
                "duration_ms": round((time.monotonic() - started) * 1000)}


if __name__ == "__main__":
    agent = Agent(LLM(), "你是一个帮用户查看本地文件的助手。回答前先用工具查证，不要猜。")
    for event in agent.query(" ".join(sys.argv[1:]) or input("> ")):
        print(event)
```

按执行顺序读一遍：

1. **用户输入进入消息列表**，然后进入最多 `max_turns` 轮的循环。
2. **请求模型**，带上 `SCHEMAS`（工具说明书）。请求失败不崩溃，产出 `error_during_execution` 并结束。
3. **把模型消息原样存回**，然后产出一个 `assistant` 事件。
4. **没有工具调用**：这就是最终回答，产出 `success` 并结束。这是正常的出口。
5. **有工具调用**：逐个执行，每个都追加一条 `tool` 消息，然后回到第 2 步。
6. **轮数用完**：产出 `error_max_turns`。这是兜底的出口，防止模型陷在“调工具 → 看结果 → 再调工具”里出不来。

`run_tool` 的原则是**任何失败都变成一段文字还给模型，而不是抛异常**。工具名写错、参数不是合法 JSON、文件不存在，这些都是模型能自己修正的错误：它看到“没有名为 xxx 的工具，可用的有 [...]”，下一轮通常就改对了。只有模型修不了的问题（网络断了、key 错了、轮数用完）才停下来告诉用户。

### 跑起来

在 `agent-life-from-zero/` 目录下运行（先按 [README](README.md) 配好环境变量）：

```bash
uv run python -m minicode.agent "这个目录里有哪些 Python 文件？chatbot.py 是做什么的？"
```

你会看到一串事件，大致是这样（模型每次的选择可能不同）：

```text
{'type': 'assistant', 'text': '', 'tool_calls': [{'name': 'list_files', 'arguments': '{}'}]}
{'type': 'tool_result', 'name': 'list_files', 'content': 'chatbot.py\nextract_json.py\nminicode/\n...'}
{'type': 'assistant', 'text': '', 'tool_calls': [{'name': 'read_file', 'arguments': '{"path": "chatbot.py"}'}]}
{'type': 'tool_result', 'name': 'read_file', 'content': 'import os\nfrom openai import OpenAI\n...'}
{'type': 'assistant', 'text': 'chatbot.py 是一个多轮对话脚本……', 'tool_calls': []}
{'type': 'result', 'subtype': 'success', 'text': 'chatbot.py 是一个多轮对话脚本……', 'num_turns': 3, 'duration_ms': 5210}
```

再试试这几个输入，对照观察循环的每一条路径：

| 输入 | 应该看到 | 验证的是 |
| --- | --- | --- |
| `你好` | 不调用工具，1 轮结束 | 模型会判断“不需要工具” |
| `读一下 nope.md` | `read_file` 返回 `FileNotFoundError`，模型向你说明 | 错误交还模型，而不是崩溃 |
| 把 `max_turns` 改成 1，再问第一个问题 | `error_max_turns` | 兜底出口 |
| 把 `LLM_API_KEY` 改错 | `error_during_execution` | 模型修不了的错 |

注意：这一版的 `read_file` 能读任何路径，没有任何限制。它只是用来理解循环的，不要拿它处理不可信的输入。第 2 章会重写工具，第 3 章加上权限。

### 离线测试

新建 `tests/test_ch01.py`：

```python
"""第 1 章的离线测试：用假模型检查循环逻辑。第 2 章换掉 agent.py 之后，删掉这个文件。"""
from minicode.agent import Agent
from minicode.testing import FakeLLM, call


def test_tool_then_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("样本B=1.37", encoding="utf-8")
    llm = FakeLLM([call("read_file", path="notes.md"), "样本B 是 1.37"])
    agent = Agent(llm, "你是助手")
    events = list(agent.query("样本B 是多少？"))
    assert events[-1]["subtype"] == "success" and events[-1]["num_turns"] == 2
    # 消息顺序：system → user → assistant(tool_calls) → tool → assistant
    assert [m["role"] for m in agent.messages] == ["system", "user", "assistant", "tool", "assistant"]
    assert agent.messages[3]["content"] == "样本B=1.37"
    # 第二次请求时，模型能看到工具结果
    assert llm.requests[1]["messages"][-1]["role"] == "tool"


def test_errors_go_back_to_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    llm = FakeLLM([[call("nope"), call("read_file", path="missing.md")], "我查不到"])
    events = list(Agent(llm, "s").query("x"))
    results = [e["content"] for e in events if e["type"] == "tool_result"]
    assert "没有名为 nope" in results[0] and "FileNotFoundError" in results[1]
    assert events[-1]["subtype"] == "success"


def test_stop_conditions():
    llm = FakeLLM([call("list_files")] * 5)
    assert list(Agent(llm, "s", max_turns=2).query("x"))[-1]["subtype"] == "error_max_turns"
    llm = FakeLLM([ConnectionError("网络断了")])
    final = list(Agent(llm, "s").query("x"))[-1]
    assert final["subtype"] == "error_during_execution" and "网络断了" in final["text"]
```

```bash
uv run python -m pytest tests/test_ch01.py -q
```

三个测试分别覆盖了“正常出口”“错误交还模型”和两个“兜底出口”。第 2 章会把 `agent.py` 整个换掉，到时候删掉这个测试文件。

### 和 Claude Code 的差别

| 方面 | Claude Code | 这一版 minicode |
| --- | --- | --- |
| 消息格式 | Anthropic Messages API：工具调用是 `tool_use` 内容块，结果放在 user 消息里的 `tool_result` 块 | OpenAI 兼容格式：`tool_calls` 字段和独立的 `tool` 消息 |
| 输出方式 | 流式输出，文字一个个蹦出来 | 一次拿到完整回复（见进阶练习 1） |
| 一轮多个工具调用 | 只读工具并行执行 | 逐个执行（第 2 章改） |
| 停止条件 | 无工具调用、轮数、预算、用户中断 | 无工具调用、轮数、模型出错（预算在第 2 章，中断在第 2 章） |

四家主流接口的循环完全一样，区别只在字段名。想换一家时，只需要改 `llm.py` 里“解析调用”和 `agent.py` 里“回传结果”的格式：

| 对比项 | OpenAI Chat Completions（本教程） | OpenAI Responses API | Claude Messages API | Gemini API |
| --- | --- | --- | --- | --- |
| 工具定义 | `{"type": "function", "function": {...}}` | `{"type": "function", name, ...}`，扁平一层 | `{name, description, input_schema}` | `function_declarations` |
| 怎么判断要调工具 | `finish_reason == "tool_calls"` | 输出里有 `function_call` 项 | `stop_reason == "tool_use"` | parts 里有 `function_call` |
| 参数类型 | JSON 字符串，要 `json.loads` | JSON 字符串 | 对象，不用解析 | 对象，不用解析 |
| 结果回传 | 单独的 `role: "tool"` 消息 | `function_call_output` 项 | user 消息里的 `tool_result` 块，可加 `is_error` | `function_response` part |

### 自检清单

- [ ] 能说出 `messages` 在一次两轮工具调用里依次多了哪几条消息。
- [ ] 能解释为什么要把 assistant 消息整条存回，而不是只存 `content`。
- [ ] 能说出循环的四个出口，以及哪些错误交还模型、哪些直接停下。
- [ ] 能用 `FakeLLM` 写一个新测试：模型连续三次调用不存在的工具。

### 常见坑

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| 400，提示 tool_calls 后面缺少 tool 消息 | 某个调用没回结果，或者顺序不对 | 每个 `tool_call_id` 都回一条，紧跟在 assistant 消息之后 |
| DeepSeek 报 400，提到 `reasoning_content` | 自己拼了 assistant 消息 | 用 `model_dump(exclude_none=True)` 存整条 |
| 401 | key 没设，或者设在了另一个终端窗口 | 在运行程序的同一个终端里 `export` |
| 模型总是凭记忆回答，不调工具 | 工具说明或系统提示词没写清楚什么时候该用 | 在系统提示词里写“回答前先用工具查证” |
| `ModuleNotFoundError: minicode` | 没在 `agent-life-from-zero/` 目录下运行，或者没用 `python -m` | 先 `cd agent-life-from-zero`，用 `python -m minicode.agent` |

### 进阶练习

1. **流式输出**：给 `LLM` 加一个 `stream=True` 的版本，文字边生成边打印。难点在于工具调用的参数也是分片到达的，要按 `index` 把 `tool_calls` 的片段拼起来。
2. 在 `result` 事件里加上本次一共用了多少 token（累加每次的 `reply.usage`）。
3. 把 `messages` 在结束时存成一个 JSON 文件。可回放的记录是调试 agent 最有用的东西，第 6 章会把它做成正式的会话记录。
4. 用 Anthropic 的 Python SDK 重写一遍循环，对照上面的表。

### 资料来源

- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works)
- [How the agent loop works（Agent SDK）](https://code.claude.com/docs/en/agent-sdk/agent-loop)
- [OpenAI Function Calling](https://platform.openai.com/docs/guides/function-calling)
- [Claude Tool Use](https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview)
- [DeepSeek API 文档](https://api-docs.deepseek.com/)
