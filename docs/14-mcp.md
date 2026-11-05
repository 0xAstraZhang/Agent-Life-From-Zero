# 第 14 章：亲手把外部进程接成一个工具

上一章：[Skills](13-skills.md) · 下一章：[评测](15-evals.md)

继续使用自己的 `my-agent/` 项目。本章新增两个文件：一个提供 echo 工具的进程，一个向它发送请求的客户端。先让真实模型完成一次“调用 → 外部进程 → 结果 → 回答”，再处理外部工具的业务错误。

这里复用你在第 03 章写的 `model.open_model()`，以及前面写的 `agent.Agent` 和工具注册表格式。`Agent` 收到的仍是普通工具；它不需要理解 MCP。

## 1. 写出只有一个工具的服务器

新建 `echo_server.py`，写入完整程序：

```python file=echo_server.py step=minimal
import json
import sys

PROTOCOL = "2025-11-25"
initialized = False

for line in sys.stdin:
    request = json.loads(line)
    method = request["method"]
    if "id" not in request:
        if method == "notifications/initialized":
            initialized = True
        continue

    reply = {"jsonrpc": "2.0", "id": request["id"]}
    if method == "initialize":
        reply["result"] = {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "lesson-echo", "version": "1"},
        }
    elif not initialized:
        reply["error"] = {"code": -32000, "message": "尚未初始化"}
    elif method == "tools/list":
        reply["result"] = {"tools": [{
            "name": "echo",
            "description": "返回输入文本；error: 前缀用于演示工具失败。",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
        }]}
    elif method == "tools/call" and request["params"]["name"] == "echo":
        text = request["params"]["arguments"]["text"]
        reply["result"] = {
            "content": [{"type": "text", "text": text}],
            "isError": text.startswith("error:"),
        }
    else:
        reply["error"] = {"code": -32601, "message": "未知方法"}
    print(json.dumps(reply, ensure_ascii=False), flush=True)
```

每一行输入得到一行 JSON 输出。服务器做三件事：初始化、公布工具、执行工具。通知没有 `id`，所以不回复。普通日志不能写进 stdout，否则客户端会把它当作协议消息。

## 2. 写出客户端的正常流程

新建 `echo_client.py`。先只连接自己刚写的服务器，读取一条正常文本结果：

```python file=echo_client.py step=minimal
import json
from pathlib import Path
import subprocess
import sys


class EchoClient:
    def __init__(self):
        self.process = None
        self.request_id = 0
        self.results = []

    def __enter__(self):
        server = Path(__file__).with_name("echo_server.py")
        self.process = subprocess.Popen(
            [sys.executable, str(server)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, text=True, encoding="utf-8",
        )
        try:
            self.request("initialize", {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "my-agent", "version": "1"},
            })
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.definition = self.request("tools/list", {})["tools"][0]
            return self
        except Exception:
            self.close()
            raise

    def send(self, message):
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def request(self, method, params):
        self.request_id += 1
        self.send({"jsonrpc": "2.0", "id": self.request_id,
                   "method": method, "params": params})
        reply = json.loads(self.process.stdout.readline())
        if reply.get("id") != self.request_id:
            raise ValueError("响应 ID 与请求不匹配")
        if "error" in reply:
            raise ValueError(reply["error"])
        return reply["result"]

    def call(self, text):
        result = self.request("tools/call", {"name": "echo", "arguments": {"text": text}})
        output = result["content"][0]["text"]
        self.results.append(output)
        return output

    def tools(self):
        return {"echo": {
            "spec": {"type": "function", "function": {
                "name": "echo", "description": self.definition["description"],
                "parameters": self.definition["inputSchema"],
            }},
            "execute": self.call,
            "read_only": True,
        }}

    def close(self):
        if self.process is None:
            return
        self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()

    def __exit__(self, *exc):
        self.close()
```

先读 `request()`：写一条请求，读一条响应，取出 `result`。再读 `call()`：把模型给出的参数放进 `tools/call`。最后读 `tools()`：把服务器公布的名字、说明和参数转成前面已有的工具格式。

`request_id` 匹配的是进程通信；模型自己的工具调用 ID 仍由 Agent 和 `tools.execute_call()` 处理。两组编号不需要相同。

## 3. 用真实模型验证最小闭环

用下面内容替换项目中的 `demo.py`：

```python file=demo.py step=minimal
from agent import Agent
from echo_client import EchoClient
from model import open_model

with open_model() as model, EchoClient() as client:
    answer = Agent(model, client.tools()).run(
        "必须调用 echo，text 精确为「你好，MCP」（不含引号），再根据工具结果回答。"
    )
    print(answer)
    passed = "你好，MCP" in client.results
    print("echo 往返验收：", passed)
    if not passed:
        raise RuntimeError("没有观察到实际的 echo 往返")
```

运行 `uv run python demo.py`。预期既有模型回答，也有 `echo 往返验收： True`。后者来自真实子进程返回的内容，模型只说“已调用”不能让它通过。

这份最小客户端只处理自己控制的服务器。`readline()` 没有请求期限，也没有处理响应之间插入的通知；暂时不要用它连接任意服务器。

## 4. 增强：让业务错误也进入工具结果

服务器已经会把 `error:` 开头的输入标为失败。接下来只增加这一件事：读取 `isError`，让模型看到错误并继续尝试。

新建 `checked_echo.py`。它继承刚才亲手写的客户端，只替换工具执行方法：

```python file=checked_echo.py step=enhanced
from echo_client import EchoClient


class CheckedEchoClient(EchoClient):
    def __init__(self):
        super().__init__()
        self.failures = []
        self.outcomes = []

    def call(self, text):
        result = self.request("tools/call", {"name": "echo", "arguments": {"text": text}})
        output = result["content"][0]["text"]
        if result.get("isError", False):
            self.failures.append(output)
            self.outcomes.append("error")
            return "工具错误：" + output
        self.results.append(output)
        self.outcomes.append("success")
        return output
```

替换 `demo.py`：

```python file=demo.py step=enhanced
from agent import Agent
from checked_echo import CheckedEchoClient
from model import open_model

with open_model() as model, CheckedEchoClient() as client:
    answer = Agent(model, client.tools()).run(
        "先调用 echo，text 为「error: unavailable」。看到工具错误后，"
        "再调用 echo，text 为「你好，MCP」。最后根据成功结果回答。"
    )
    print(answer)
    passed = (bool(client.failures) and "你好，MCP" in client.results
              and client.outcomes[0] == "error" and client.outcomes[-1] == "success")
    print("错误回传后恢复：", passed)
    if not passed:
        raise RuntimeError("没有完成失败回传和后续成功调用")
```

再次运行 `uv run python demo.py`，观察错误成为下一轮模型的输入，而不是退出整个程序。

## 5. 再决定需要哪些通用能力

我们已经手写了握手、发现一个工具、协议桥接、业务错误回传和进程回收。外部工具仍使用普通 Agent Loop。

接入其他服务器前，再逐项实现并测试：初始化版本和能力核对、分页、通知、请求超时、非文本结果、工具名冲突。工具是否只读由本地判断；本章的 `read_only=True` 只适用于自己写的 echo 服务，不能从陌生服务器的声明直接推导。

仓库中的 [完整 MCP 参考实现](../minicode/mcp.py)处理更多通信边界，可以在完成手写版后对照。它不属于本章已经写出的代码。协议原文见 [MCP 2025-11-25 tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。
