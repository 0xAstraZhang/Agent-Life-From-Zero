# 第 8 章：自己读出文本增量，再接回工具循环

[上一章：权限](07-permissions.md) · [目录](README.md) · [下一章：上下文](09-context.md)

继续自己的 `my-agent` 项目。前面等待完整 JSON 后才显示回答；现在直接用已经安装的 `httpx` 读取 SSE。先显示一段真实文本，再拼出 Agent 已认识的完整回复。

本章沿用 Chat Completions。`model.open_model()` 仍是你第 00 章写的连接配置；模型客户端公开 `model`、`base_url`、`api_key` 和 `client`，供这里复用。

## 1. 先定义如何读一个 SSE 事件

新建 `streaming.py`，完整写入：

```python file=streaming.py step=text
import json


def sse_data(response):
    data = []
    for line in response.iter_lines():
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield "\n".join(data)


def stream_text(chat, prompt):
    body = {"model": chat.model, "stream": True,
            "messages": [{"role": "user", "content": prompt}]}
    text, reason, finished = [], None, False
    with chat.client.stream(
        "POST", chat.base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {chat.api_key}"}, json=body,
    ) as response:
        response.raise_for_status()
        for data in sse_data(response):
            if data == "[DONE]":
                finished = True
                break
            chunk = json.loads(data)
            if chunk.get("error"):
                raise RuntimeError("模型在生成过程中返回错误")
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {}).get("content")
                if delta:
                    text.append(delta)
                    print(delta, end="", flush=True)
                if choice.get("finish_reason"):
                    reason = choice["finish_reason"]
    if not finished or reason != "stop":
        raise RuntimeError("没有收到完整的文本回复")
    if not "".join(text).strip():
        raise RuntimeError("没有收到文本增量")
    return "".join(text)
```

先看 `sse_data()`：空行标记一个事件结束，多行 `data:` 组成同一个事件。`stream_text()` 再解析 JSON，从 `delta.content` 取文字立即显示。代码最后检查结束标记与停止原因，避免把连接断开当成完整回答。

## 2. 先让终端显示文字

完整替换 `demo.py`：

```python file=demo.py step=text
from model import open_model
from streaming import stream_text


def main():
    with open_model() as chat:
        stream_text(chat, "用两句话解释 Agent 怎样使用工具。")
    print("\n文本已完整接收")


if __name__ == "__main__":
    main()
```

运行第一个检查点：

```bash
uv run python demo.py
```

文字会逐段到达，最后显示“文本已完整接收”。这一步没有工具，也不需要修改 Agent。你现在已经亲手写出了从 HTTP 流到终端输出的路径。

## 3. 再拼出一条完整回复

接下来加入工具。模型可能先发 `{"a":`，下一段才发 `2,"b":3}`。先把参数按调用的 `index` 分别拼起来；不能收到一段参数就运行函数。

完整替换 `streaming.py`。新的类继续提供 `.complete(messages, tools)`，因此已经写好的 Agent 可以直接使用它：

```python file=streaming.py step=tools
import json


def sse_data(response):
    data = []
    for line in response.iter_lines():
        if line == "":
            if data:
                yield "\n".join(data)
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield "\n".join(data)


class StreamingChatModel:
    def __init__(self, chat):
        self.chat = chat

    def complete(self, messages, tools=()):
        body = {"model": self.chat.model, "messages": messages, "stream": True}
        if tools:
            body["tools"] = list(tools)
        text, calls, reasoning = [], {}, []
        reason, finished = None, False
        with self.chat.client.stream(
            "POST", self.chat.base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {self.chat.api_key}"}, json=body,
        ) as response:
            response.raise_for_status()
            for data in sse_data(response):
                if data == "[DONE]":
                    finished = True
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise RuntimeError("模型在生成过程中返回错误")
                for choice in chunk.get("choices", []):
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        text.append(delta["content"])
                        print(delta["content"], end="", flush=True)
                    if delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                    for part in delta.get("tool_calls", []):
                        index = part["index"]
                        if index not in calls:
                            calls[index] = {"id": "", "type": "function",
                                            "function": {"name": "", "arguments": ""}}
                        call = calls[index]
                        call["id"] += part.get("id") or ""
                        function = part.get("function", {})
                        call["function"]["name"] += function.get("name") or ""
                        call["function"]["arguments"] += function.get("arguments") or ""
                    if choice.get("finish_reason"):
                        reason = choice["finish_reason"]
        if not finished or reason is None:
            raise RuntimeError("流在完整回复到达之前结束")
        message = {"role": "assistant", "content": "".join(text)}
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        return {"message": message, "finish_reason": reason}
```

先追文本：到达时显示，结束后合成 `message.content`。再追工具：参数分片只写进 `calls`，完整结束后才作为 `message.tool_calls` 返回。两条路径在最后汇合成与前章相同的 `message`、`finish_reason`。

适配器里面没有执行工具。Agent 拿到完整回复后，仍按原来的顺序记录助手消息、调用 runner、记录结果、再次请求模型。

## 4. 让已有 Agent 使用这个类

完整替换 `demo.py`：

```python file=demo.py step=tools
from agent import Agent
from model import open_model
from streaming import StreamingChatModel


TOOLS = {

        "add": {
            "spec": {"type": "function", "function": {
                "name": "add", "description": "把两个整数相加",
                "parameters": {"type": "object", "properties": {
                    "a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"], "additionalProperties": False}}},
            "execute": lambda a, b: str(a + b), "read_only": True,
        }

}


def main():
    with open_model() as chat:
        agent = Agent(StreamingChatModel(chat), TOOLS)
        agent.run("请调用 add 计算 2 加 3，再回答。")
    results = [message for message in agent.history if message["role"] == "tool"]
    if not results:
        raise RuntimeError("没有发生工具往返")
    if results[-1]["content"] != "5":
        raise RuntimeError("工具没有返回正确结果")
    print("\n工具结果已回传：", results[-1]["content"])


if __name__ == "__main__":
    main()
```

再运行 `uv run python demo.py`。模型先请求加法，收到本地结果后逐段显示回答，末尾说明工具结果已经回传。由于文本已经显示，不再打印 `Agent.run()` 的整段返回值。

要保留上一章审批，只需继续传入已经写好的 `runner`；流式输出没有改变工具执行的入口。

## 5. 最后用失败说明边界

正常文本和工具往返都跑通后，再分别删除 `[DONE]`、截断参数字符串、把停止原因改为 `length`。没有完整回复时，StreamingChatModel 报错；已收到截断回复时，前章 Agent 的停止规则阻止工具执行。

然后再考虑多个工具参数交错、流中断时关闭连接、用户中断工具时补记结果、最后才出现的用量。`with client.stream(...)` 已负责关闭响应；同一轮多个调用按 `index` 存放，参数不会混在一起。兼容服务返回的 `reasoning_content` 随助手消息保留，不作为普通文字显示。

章末可选参考：[仓库完整流式 Agent](../lessons/stages/08/minicode/agent.py)、[仓库实验](../examples/08_streaming.py)。当前手写项目不导入这些实现。
