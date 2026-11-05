# 第 4 章（选修）：用 Responses 完成同一个工具往返

[上一章：把循环提取为 Agent](03-chat-completions.md) · [目录](README.md) · [继续主线：编码工具](06-coding-tools.md)

在你前面手写的项目目录里继续。本章先发两次真实 HTTP 请求，再把协议转换接回已经写好的 Agent。`model.py` 中的 `open_model()` 来自第 00 章，负责读取配置、创建并关闭 HTTP 客户端；`agent.py` 来自第 03 章。

本章是选修。只用 Chat Completions 的读者可以直接去第 06 章。练习 Responses 时，环境变量中的地址应是支持 `/responses` 的 API 根地址，模型也要支持该协议。

## 1. 先定义发送请求的函数

新建 `responses_request.py`，完整写入：

```python file=responses_request.py step=minimal
def request_response(chat, items, tools):
    response = chat.client.post(
        chat.base_url.rstrip("/") + "/responses",
        headers={"Authorization": f"Bearer {chat.api_key}"},
        json={"model": chat.model, "input": items, "tools": tools,
              "store": False, "include": ["reasoning.encrypted_content"]},
    )
    response.raise_for_status()
    return response.json()
```

它接收对话 items 和工具说明，返回原始响应。`store=False` 表示我们自己保存历史；请求保留续轮所需的加密推理内容。当前只需原样带回这些数据，不需要先理解其内部结构。

## 2. 写出两次请求之间发生的事

把本章的 `demo.py` 完整写成下面这样。加法在本地 Python 中执行，返回值通过 `function_call_output` 发回模型：

```python file=demo.py step=minimal
import json

from model import open_model
from responses_request import request_response


def main():
    parameters = {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False}
    tools = [{"type": "function", "name": "add", "description": "两个整数相加",
              "parameters": parameters, "strict": False}]
    items = [{"role": "user", "content": "请调用 add 计算 2 加 3，再回答。"}]
    with open_model() as chat:
        first = request_response(chat, items, tools)
        if first["status"] != "completed":
            raise RuntimeError("响应还未完成，不能执行工具")
        items.extend(first["output"])
        executed = 0
        for item in first["output"]:
            if item["type"] == "function_call":
                if item["name"] != "add":
                    raise ValueError("这个原型只实现 add")
                args = json.loads(item["arguments"])
                result = str(args["a"] + args["b"])
                print("本地工具结果：", result)
                items.append({"type": "function_call_output",
                              "call_id": item["call_id"], "output": result})
                executed += 1
        if executed == 0:
            raise RuntimeError("模型没有调用工具，请检查模型的工具能力")
        final = request_response(chat, items, tools)
        if final["status"] != "completed":
            raise RuntimeError("最终响应未完成")
        for item in final["output"]:
            if item["type"] == "function_call":
                raise RuntimeError("模型还想继续调用；下一步接回 Agent 循环")
            if item["type"] == "message":
                for block in item["content"]:
                    if block["type"] == "output_text":
                        print(block["text"])


if __name__ == "__main__":
    main()
```

运行第一个检查点：

```bash
uv run python demo.py
```

应先看到本地工具结果 5，再看到模型回答。沿着变量 `items` 看：第一次只有用户消息；第二次包含原始 `output` 和对应的工具结果。这就是本章的最小闭环。

`function_call.id` 标识一个响应 item，`call_id` 才把调用与结果连起来。结果里使用 `call_id`，不能拿 item 的 `id` 代替。响应仍有调用时，模型这次已经说完，Agent 的任务却还没完成。

## 3. 跑通后，再提取协议适配器

刚才的程序固定做两次请求。接下来把数据转换放进 `ResponsesModel.complete()`，让它返回第 03 章 Agent 已认识的 `message` 和 `finish_reason`。新建 `responses_adapter.py`：

```python file=responses_adapter.py step=enhanced
import copy

from responses_request import request_response


class ResponsesModel:
    def __init__(self, chat):
        self.chat = chat

    def complete(self, messages, tools=()):
        items = []
        for message in messages:
            if message["role"] == "tool":
                items.append({"type": "function_call_output",
                              "call_id": message["tool_call_id"],
                              "output": message["content"]})
            elif message.get("_responses_output") is not None:
                items.extend(copy.deepcopy(message["_responses_output"]))
            else:
                if message.get("content"):
                    items.append({"role": message["role"], "content": message["content"]})
                for call in message.get("tool_calls", []):
                    items.append({"type": "function_call", "call_id": call["id"],
                                  "name": call["function"]["name"],
                                  "arguments": call["function"]["arguments"]})
        wire_tools = []
        for spec in tools:
            function = spec["function"]
            wire_tools.append({"type": "function", "name": function["name"],
                               "description": function.get("description", ""),
                               "parameters": function["parameters"], "strict": False})
        response = request_response(self.chat, items, wire_tools)
        text, calls = [], []
        for item in response.get("output", []):
            if item["type"] == "function_call":
                calls.append({"id": item["call_id"], "type": "function", "function": {
                    "name": item["name"], "arguments": item["arguments"]}})
            elif item["type"] == "message":
                text.extend(block["text"] for block in item["content"] if block["type"] == "output_text")
        reason = "tool_calls" if calls else "stop"
        if response.get("status") != "completed":
            reason = "length" if response.get("status") == "incomplete" else "error"
        message = {"role": "assistant", "content": "".join(text),
                   "_responses_output": copy.deepcopy(response.get("output", []))}
        if calls:
            message["tool_calls"] = calls
        return {"message": message, "finish_reason": reason}
```

转换分成两段：前半段把已有历史变成 `input`，后半段把响应变回 Agent 的消息。原始 `output` 存在助手消息的 `_responses_output` 中；下一轮继续使用它，避免丢掉仅靠文本和工具字段无法表达的数据。

## 4. 接回已有循环

现在完整替换 `demo.py`。这里的 Agent 是你第 03 章手写的类，不需要重新写一个循环：

```python file=demo.py step=enhanced
from agent import Agent
from model import open_model
from responses_adapter import ResponsesModel


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
        agent = Agent(ResponsesModel(chat), TOOLS)
        print(agent.run("请调用 add 计算 2 加 3，再回答。"))
    results = [message for message in agent.history if message["role"] == "tool"]
    if not results:
        raise RuntimeError("模型没有实际使用工具")
    print("工具结果已回传：", results[-1]["content"])


if __name__ == "__main__":
    main()
```

再运行 `uv run python demo.py`。工具结果仍然是 5，但多轮历史与继续/停止由 Agent 管理。

## 5. 最后再检查边界

先确认正常往返，再分别制造这些条件：响应变成 incomplete、工具名称不存在、参数不是合法 JSON、模型连续请求多轮工具。截断在适配器中变成非正常停止状态；工具名称与参数错误交给前章的执行器；轮数上限仍由 Agent 管理。

本章保留的是同协议、同一条模型会话的原始输出。编辑历史或切换模型时，需要另外制定哪些原始数据仍可使用的规则，再引入来源标记与内容指纹。

章末可选参考：[仓库适配器](../lessons/stages/04/minicode/providers/responses.py)、[仓库实验](../examples/04_responses.py)。它们用于对照，不参与上面手写项目的导入。
