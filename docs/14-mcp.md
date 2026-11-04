# 第 14 章：用 MCP 接入外部工具

到这里，模型侧已经通过统一接口连接三种协议，工具侧也有了本地执行与权限边界。
这一章把工具搬到另一个进程，Agent 仍然只接收 `ToolSpec`、发出 `ToolCall`、得到 `ToolResult`。

新增文件：

- [minicode/mcp.py](../minicode/mcp.py)：stdio 客户端和工具转换。
- [examples/mcp_demo_server.py](../examples/mcp_demo_server.py)：本地 echo 服务器。
- [examples/14_mcp.py](../examples/14_mcp.py)：把服务器工具接进 Agent。

本章固定实现 **MCP 2025-11-25 的 stdio 与 tools 子集**。
它要求服务器支持这一版本，不把这个教学实现称为所有 MCP 版本的通用客户端。
Streamable HTTP、OAuth、资源、提示词、sampling 和异步任务不在本章实现范围内。

## 本章的完整累积目录

导出只包含第 00–14 章文件的工作目录，再在里面运行示例和本章测试：

```bash
uv run python scripts/check_tutorial.py --chapter 14 --export /tmp/minicode-ch14
cd /tmp/minicode-ch14
uv sync
uv run python -m pytest tests -q
```

目标必须是新目录或空目录。导出目录的 README 列出本章新增、替换的完整文件和实验命令。这些文件可直接编辑；隔离验收会阻止它从原仓库加载尚未学习的模块。

## 1. 模型适配与工具适配是两个方向

```text
OpenAI / Anthropic
       ↕ 模型协议适配器
     Agent
       ↕ ToolCall / ToolResult
 PermissionRunner
       ↕
   ToolRunner
       ↕ 普通 Tool.execute
   MCPClient
       ↕ JSON-RPC over stdio
  外部工具进程
```

模型适配器不执行 MCP 请求，MCP 客户端也不发模型请求。
两者在内部工具类型处相遇。
因此本章不会再次出现 `tool_calls[].function`、`function_call_output` 或 Anthropic 的 `tool_result` 编码代码。

## 2. 先看一条完整的协议往返

stdio 使用子进程的标准输入和标准输出传输 UTF-8 JSON-RPC，每条消息占一行。
服务器的日志写入 stderr，stdout 只包含协议消息。
这些是本章使用的 [stdio 传输规则](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)。

客户端先启动显式提供的命令，然后进行握手：

```text
client → initialize
server ← protocolVersion、capabilities、serverInfo
client → notifications/initialized
client → tools/list
server ← 工具名称、说明和 inputSchema
client → tools/call
server ← content、isError
```

初始化请求包括：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-11-25",
    "capabilities": {},
    "clientInfo": {"name": "minicode", "version": "0.1.0"}
  }
}
```

客户端只声明自己真正支持的能力。
收到响应后，检查版本一致、服务器声明了 `tools`，再发送 initialized 通知。
这一步失败就关闭进程，不能继续把任意 JSON 当作工具服务。
完整生命周期见 [MCP 2025-11-25 Lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)。

## 3. 请求 ID 和工具调用 ID 各管一件事

MCP 的 JSON-RPC `id` 用来匹配一次进程通信。
内部 `ToolCall.id` 用来匹配模型提出的调用与 Agent 返回的工具结果。
它们不必相同。

例如，模型提出 `call_7`，客户端可能用 JSON-RPC `id=12` 调用远端 `echo`。
服务端回复 `id=12` 后，`ToolRunner` 将结果封装回 `ToolResult(call_id="call_7", ...)`。
模型只能看到属于自己的调用 ID。

`MCPClient.request()` 使用锁保证同一进程一次只有一个待处理请求。
后台线程读取 stdout，把整行消息放入有上限的队列。
主线程按请求 ID 等待响应，并处理期间到达的通知。

服务器可能向客户端发出请求。
本章只回应 `ping`；未实现的方法返回 JSON-RPC `-32601`，不能假装成功。
通知不需要响应，也不能无限延长当前请求的截止时间。

## 4. 将远端定义变成普通工具

`tools/list` 支持分页，因此客户端继续读取 `nextCursor`，直到没有下一页。
重复 cursor、重复工具名和缺失 schema 都是错误。

远端定义只需要这几个字段：

```json
{
  "name": "echo",
  "description": "返回给定文本",
  "inputSchema": {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
    "additionalProperties": false
  }
}
```

转换后，模型看到的内部名称带服务器前缀，例如 `mcp__demo__echo`。
执行函数保留原始远端名称 `echo`，调用 `tools/call` 时使用它。
过长或包含特殊字符的名字会加入稳定摘要，避免简单替换造成名称冲突。

```python
def execute(arguments, _ctx):
    result = client.request("tools/call", {
        "name": remote_name,
        "arguments": arguments,
    })
    return ToolResult("", render(result), bool(result.get("isError")))

tool = Tool(ToolSpec(local_name, description, input_schema), execute)
```

本章呈现文本内容；只有结构化结果时转成 JSON 文本。
图片、音频等内容块会明确标记为尚不支持，不能默默当成空结果。
工具定义和结果的规范在 [MCP Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) 中。

`isError` 表示工具业务执行失败；JSON-RPC `error` 则表示协议请求失败。
两者都要传递为错误，但调试时需要知道发生在哪一层。

## 5. 远端的“只读提示”不等于本地授权

MCP 工具可以声明 `readOnlyHint`。
这是服务器提供的元数据；不可信服务器也可以填写 `true`。
本章的转换器因此把所有 MCP 工具设为 `read_only=False`、`parallel_safe=False`。

调用必须经过第 7 章的 PermissionRunner，由用户审批，或显式加入允许列表。
只读子 Agent 默认无法使用这些未经本地验证的外部工具。

连接入口同样明确。下面是接入累积应用的完整组装段，沿用第 11 章的 `assemble()`、`permission_options` 和此前已有的 `tools` 列表：

```python
with MCPClient([sys.executable, str(server)], name="demo") as client:
    tools_with_mcp = [*tools, *client.list_tools()]
    permission_options["allowed"] = (*permission_options["allowed"], "mcp__demo__echo")
    runner = assemble(tools_with_mcp)
    agent = Agent(model, runner, system_prompt)
    for event in agent.run("调用 echo 工具"):
        print(event)
```

这里明确允许的是本章已知的 echo 工具，已有 allow 列表、只读模式、审批对象和 hooks 都保留。
不要用远端工具列表覆盖原有编码工具、计划、Delegate 和 Skill。
消费 Agent 事件必须在 `with` 内完成；退出后 MCP 进程已关闭，不能继续调用这些工具。
本章的独立实验只注册 echo，便于观察进程通信；累积应用使用上面的追加方式。

程序不会扫描项目配置并自动启动其中的命令。
**启动 MCP 服务器本身就会执行本地程序**，发生在工具调用审批之前。
因此应由用户明确选择服务器命令，并先了解其权限和行为。

## 6. 超时之后，结果可能未知

请求使用单调时钟计算绝对截止时间。
如果服务器不断发送通知，截止时间也不会重新计时。

超时后客户端发送取消通知，关闭会话，并报告“工具是否已产生影响未知”。
不能自动重试写入工具：第一次调用可能已经成功，只是响应丢失。

退出 `with` 时先关闭服务器 stdin，让服务端正常退出。
若未退出，再发送终止信号；仍未退出则强制终止并回收进程。
POSIX 下还清理本次会话的进程组，避免后台子进程继续持有 stdout。

本章忽略 stderr 日志以保持示例简洁。
实际诊断需要保留有上限的 stderr 记录；它仍不能混入 JSON-RPC stdout。

## 7. 运行真正的进程通信

```bash
uv run python examples/14_mcp.py
```

这个示例不访问网络，也不使用 API Key。
模型是 FakeModel，MCP 通信却使用真实子进程和 stdin/stdout。
服务器执行握手、公布 echo 工具、返回文本，最后正常退出。

预期输出：

```text
你好，MCP
success 外部工具返回了问候。
服务器已退出: True
```

把示例里的文本改为 `error: unavailable`，服务器就会返回 `isError=true`。
观察 `tool_result` 事件的错误状态如何进入内部历史；无需修改三个模型适配器。

## 8. 检查通信边界

```bash
uv run python -m pytest tests/test_mcp.py -q
```

[测试](../tests/test_mcp.py) 覆盖正常握手和调用、业务错误、默认权限拒绝、版本不兼容、进程超时、非法 stdout、分页和服务器 ping。
每个测试结束后都验证进程已回收，避免测试通过却留下后台服务。

## 练习

1. 给 demo server 增加第二个只读工具，观察工具发现是否需要修改 Agent。
2. 把某个工具分到 tools/list 的第二页，验证分页结果的工具名仍然唯一。
3. 为可信服务器增加本地审核过的只读工具名单，不直接信任远端 hint。

到这里，模型协议和外部工具协议都在各自的边界完成转换。
下一章用固定任务和检查结果评估整个 Agent，而不是只检查单个模块。

[上一章：Skills](13-skills.md) · [目录](README.md) · [下一章：评测](15-evals.md)
