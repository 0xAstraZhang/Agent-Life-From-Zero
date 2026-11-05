# 第 5 章（选修）：用 Messages 表达调用与结果

[Chat Completions](03-chat-completions.md) · [目录](README.md) · [继续主线：编码工具](06-coding-tools.md)

在前面手写的项目里继续。本章不依赖 Responses 的实现，只复用第 00 章的 `model.open_model()` 和第 03 章的 Agent。先将环境变量设置为支持 Anthropic Messages 的模型与 API 根地址；请求函数会添加 `/messages`。

## 1. 先写一个请求函数

新建 `messages_request.py`，完整写入：

```python file=messages_request.py step=minimal
def request_messages(chat, messages, tools, system=""):
    body = {"model": chat.model, "max_tokens": 1024,
            "messages": messages, "tools": tools}
    if system:
        body["system"] = system
    response = chat.client.post(
        chat.base_url.rstrip("/") + "/messages",
        headers={"x-api-key": chat.api_key, "anthropic-version": "2023-06-01"},
        json=body,
    )
    response.raise_for_status()
    return response.json()
```

这里新增的协议知识只有请求头、`max_tokens` 与顶层 `system`。函数仍然只是发请求并返回 JSON。

## 2. 先跑通一条工具调用

新建或替换 `demo.py`：

```python file=demo.py step=minimal
from model import open_model
from messages_request import request_messages


def main():
    parameters = {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False}
    tools = [{"name": "add", "description": "两个整数相加", "input_schema": parameters}]
    messages = [{"role": "user", "content": "请调用 add 计算 2 加 3，再回答。"}]
    with open_model() as chat:
        first = request_messages(chat, messages, tools)
        if first["stop_reason"] != "tool_use":
            raise RuntimeError("模型没有给出完整工具调用")
        messages.append({"role": "assistant", "content": first["content"]})
        results = []
        for block in first["content"]:
            if block["type"] == "tool_use":
                if block["name"] != "add":
                    raise ValueError("这个原型只实现 add")
                result = str(block["input"]["a"] + block["input"]["b"])
                print("本地工具结果：", result)
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})
        if not results:
            raise RuntimeError("回复中没有工具调用")
        messages.append({"role": "user", "content": results})
        final = request_messages(chat, messages, tools)
        if final["stop_reason"] not in {"end_turn", "stop_sequence"}:
            raise RuntimeError("模型尚未结束回答")
        for block in final["content"]:
            if block["type"] == "text":
                print(block["text"])


if __name__ == "__main__":
    main()
```

运行第一个检查点：

```bash
uv run python demo.py
```

这次调用在 assistant 的 `tool_use` 中，参数 `input` 已经是对象。结果放在下一条 user 消息的 `tool_result` 中，用 `tool_use_id` 与调用关联。先看到本地结果，再看到模型回答，说明闭环成立。

原助手 `content` 整体回到历史。我们暂时不拆分其中的思考块，避免在第二次请求时遗漏它们。

## 3. 再把转换放进一个类

现在要复用原来的 Agent。新建 `messages_adapter.py`，将 Messages 的数据转换为前章 Agent 认识的 Chat 消息与停止原因：

```python file=messages_adapter.py step=enhanced
import copy
import json

from messages_request import request_messages


class MessagesModel:
    def __init__(self, chat):
        self.chat = chat

    def complete(self, messages, tools=()):
        system = "\n".join(message["content"] for message in messages if message["role"] == "system")
        wire = []
        for message in messages:
            role = message["role"]
            if role == "system":
                continue
            if role == "tool":
                result = {"type": "tool_result", "tool_use_id": message["tool_call_id"],
                          "content": message["content"]}
                if wire and wire[-1]["role"] == "user" and wire[-1].get("_results"):
                    wire[-1]["content"].append(result)
                else:
                    wire.append({"role": "user", "content": [result], "_results": True})
                continue
            if message.get("_messages_content") is not None:
                content = copy.deepcopy(message["_messages_content"])
            else:
                content = []
                if message.get("content"):
                    content.append({"type": "text", "text": message["content"]})
                for call in message.get("tool_calls", []):
                    content.append({"type": "tool_use", "id": call["id"],
                                    "name": call["function"]["name"],
                                    "input": json.loads(call["function"]["arguments"])})
            wire.append({"role": role, "content": content})
        for message in wire:
            message.pop("_results", None)
        wire_tools = []
        for spec in tools:
            function = spec["function"]
            wire_tools.append({"name": function["name"], "description": function.get("description", ""),
                               "input_schema": function["parameters"]})
        response = request_messages(self.chat, wire, wire_tools, system)
        text, calls = [], []
        for block in response["content"]:
            if block["type"] == "text":
                text.append(block["text"])
            elif block["type"] == "tool_use":
                calls.append({"id": block["id"], "type": "function", "function": {
                    "name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
        reason = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls",
                  "max_tokens": "length"}.get(response["stop_reason"], "error")
        message = {"role": "assistant", "content": "".join(text),
                   "_messages_content": copy.deepcopy(response["content"])}
        if calls:
            message["tool_calls"] = calls
        return {"message": message, "finish_reason": reason}
```

先追一条调用和一条结果：`ToolCall` 对应 `tool_use`，历史中的 tool 消息对应 user 中的 `tool_result`。然后再看多个结果：连续的 tool 消息合并为一个 user 内容数组，先交齐上一轮的结果，再继续普通对话。

## 4. 让原来的 Agent 使用它

完整替换 `demo.py`：

```python file=demo.py step=enhanced
from agent import Agent
from model import open_model
from messages_adapter import MessagesModel


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
        agent = Agent(MessagesModel(chat), TOOLS)
        print(agent.run("请调用 add 计算 2 加 3，再回答。"))
    results = [message for message in agent.history if message["role"] == "tool"]
    if not results:
        raise RuntimeError("模型没有实际使用工具")
    print("工具结果已回传：", results[-1]["content"])


if __name__ == "__main__":
    main()
```

再次运行 `uv run python demo.py`。你没有复制 Agent 循环；模型数据在适配器两端转换，工具与历史编排仍使用自己前章写的代码。

## 5. 正常流程之后再补细节

先令模型一轮发出两个调用，观察下一次请求中的结果是否一起回传。再制造参数错误或截断：工具错误继续通过带调用 ID 的结果回传，`max_tokens` 对应非正常结束，不能执行截断调用。

本章用 `_messages_content` 保存原助手内容，支持同协议的连续会话。原生 `is_error`、更细的拒绝分类、模型切换后的原始数据处理，以及缓存用量，都是建立在这个闭环上的后续增量。不要为了先了解这些细节而推迟第一次请求。

章末可选参考：[仓库完整适配器](../lessons/stages/05/minicode/providers/anthropic.py)、[仓库实验](../examples/05_anthropic.py)。手写项目不需要导入这些文件。
