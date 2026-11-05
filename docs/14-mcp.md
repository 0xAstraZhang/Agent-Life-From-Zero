# 第 14 章：把一个外部工具接进 Agent

上一章：[Skills](13-skills.md) · 下一章：[评测](15-evals.md)

本章先完成一个小目标：模型调用 `echo`，另一个 Python 进程返回原文，模型收到结果后回答。Agent Loop 不需要改变；变化只在工具的执行函数里。

先导出本章目录，模型配置沿用[第 00 章](00-setup.md)：

```bash
uv run python scripts/check_tutorial.py --chapter 14 --export /tmp/minicode-ch14
cd /tmp/minicode-ch14
uv sync
uv run python examples/14_mcp.py --step minimal
```

模型默认使用真实 API。看到工具结果“你好，MCP”和最终回答后，再继续。完整练习文件是 [lessons/ch14.py](../lessons/ch14.py)，服务器是 [mcp_demo_server.py](../examples/mcp_demo_server.py)。最小版只连接这个已知服务器；增强版才接入通用客户端。

## 1. 先把一次函数调用搬到另一个进程

原来的执行函数直接调用本地 Python 函数。现在它发送一行 JSON，再读取一行 JSON：

```text
Agent → Tool.execute → 服务器 stdin
Agent ← ToolResult  ← 服务器 stdout
```

本章使用 MCP 2025-11-25 的 stdio/tools 子集。一行是一条 UTF-8 JSON-RPC 消息；日志写 stderr。先只处理本仓库服务器的正常响应，把下面的函数写进练习文件：

```python
import json


def request(process, request_id, method, params):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    process.stdin.write(json.dumps(message) + "\n")
    process.stdin.flush()
    reply = json.loads(process.stdout.readline())
    if reply.get("id") != request_id:
        raise ValueError("响应不属于本次请求")
    if "error" in reply:
        raise ValueError(reply["error"])
    return reply["result"]
```

`process` 由完整文件中的 `echo_process()` 创建。它只启动本仓库服务器，并在退出时关闭和回收进程。`request_id` 用来配对进程通信；它和模型的 `ToolCall.id` 是两组不同的编号。

## 2. 握手，然后发现一个工具

通信顺序很短：

```text
initialize → notifications/initialized → tools/list → tools/call
```

先声明客户端和协议版本，再通知服务器初始化完成，最后取出唯一的 echo 定义：

```python
request(process, 1, "initialize", {
    "protocolVersion": "2025-11-25", "capabilities": {},
    "clientInfo": {"name": "lesson-14", "version": "1"},
})
process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
process.stdin.flush()
definition = request(process, 2, "tools/list", {})["tools"][0]
```

`definition` 给出 `name`、`description` 和 `inputSchema`。它们正好可以变成前面学过的 `ToolSpec`。

## 3. 写出 Tool 到 MCP 的桥

把刚才的握手放进 `echo_tool()`，在函数内保存通信编号，再定义执行函数。下面是完整的桥接函数，它复用上一节的 `request()`：

```python
from minicode.tool import Tool
from minicode.types import ToolSpec


def echo_tool(process):
    request(process, 1, "initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "lesson-14", "version": "1"},
    })
    process.stdin.write('{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
    process.stdin.flush()
    definition = request(process, 2, "tools/list", {})["tools"][0]
    next_id = 2

    def execute(arguments, context):
        nonlocal next_id
        next_id += 1
        result = request(process, next_id, "tools/call", {
            "name": definition["name"], "arguments": arguments,
        })
        return result["content"][0]["text"]

    return Tool(ToolSpec("mcp__demo__echo", definition["description"],
                         definition["inputSchema"]), execute)
```

模型看到的名称是 `mcp__demo__echo`，服务器收到的是 `echo`。`ToolRunner` 仍负责把字符串变成对应模型调用 ID 的 `ToolResult`。这就是外部工具接入 Agent 的骨架。

完整文件把这个 Tool 交给原来的 `PermissionRunner → ToolRunner`，只允许已知的 echo 工具。运行后改变发送的文本，确认返回的是服务器实际收到的内容。

最小版依赖已知服务器一次只返回一条文本，不能拿来连接任意服务器。下一步保留桥接关系，补齐通信处理。

## 4. 增强检查点：接入完整 MCPClient

```bash
uv run python examples/14_mcp.py --step enhanced
```

增强版用 [minicode/mcp.py](../minicode/mcp.py) 替换简单的 `request()` 和 `echo_tool()`。Agent 部分保持同样的数据流：

```python
with MCPClient([sys.executable, str(server)], name="demo") as client:
    tools = client.list_tools()
    runner = PermissionRunner(ToolRunner(tools, ToolContext(root)),
                              allowed=["mcp__demo__echo"])
    agent = Agent(model, runner)
    for event in agent.run("调用 echo 工具"):
        print_event(event)
```

接回前面章节的累积应用时，把 `client.list_tools()` **追加**到已有工具列表，再通过原来的 `assemble()` 组装。保留原审批对象、hooks、计划、Skill 和 Delegate。

按下面的顺序扩展最小客户端，每次只改变一种条件：

| 观察到的问题 | 增加的实现 |
| --- | --- |
| 服务器版本或能力不同 | 核对初始化结果；不匹配就结束连接 |
| 工具列表有第二页 | 跟随 `nextCursor`；检测重复 cursor 和工具名 |
| 返回工具业务错误 | 保留 `isError`，转成内部错误结果 |
| 响应前插入通知或 ping | 按消息形状分流，继续等待原请求 ID |
| 服务器不再回答 | 后台读取与有界队列，主线程按固定期限等待 |
| 进程不退出 | 关闭 stdin，依次终止并回收进程组 |

完整客户端还处理命名冲突、结构化结果、非法 stdout 和不支持的内容块。`isError` 是工具业务失败，JSON-RPC `error` 是协议请求失败；两者都不能伪装成正常文本。

超时后工具可能已经产生影响。关闭连接并报告结果未知，不自动重复写入调用。通知到达也不能重新计算截止时间。

## 5. 在哪里保留执行边界

MCP 服务器是用户显式选择的本地程序，启动它就已经执行了程序。教程只运行仓库内可阅读的 echo 服务器，不自动搜索项目配置并启动命令。

外部服务器的 `readOnlyHint` 不是本地授权。完整转换器把外部工具默认设为非只读、不可并行，仍通过已有审批链。只读子 Agent 不直接继承这些权限。

本章实现不包含 Streamable HTTP、OAuth、资源、提示词、sampling 或异步任务。协议细节见 [生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)、[stdio 传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)和[工具定义](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。

## 验收与练习

```bash
uv run python examples/14_mcp.py --step minimal --offline
uv run python examples/14_mcp.py --step enhanced --offline
uv run python -m pytest tests/test_lesson14.py tests/test_mcp.py -q
```

`--offline` 只把模型替换为确定剧本，进程通信仍然真实发生。它用于回归，不代替前面真实模型选择工具的实验。

先给服务器添加第二个正常工具，观察 Agent Loop 是否需要修改。再在增强版发送 `error: unavailable`，检查业务错误能否完整回传。最后添加分页或超时，逐项验证新增分支。
