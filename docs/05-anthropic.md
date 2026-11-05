# 第 5 章（选修）：把同一个工具接到 Anthropic Messages

[Chat Completions](03-chat-completions.md) · [Responses 选修](04-responses.md) · [目录](README.md) · [继续主线：编码工具](06-coding-tools.md)

本章把已经跑通的工具闭环换成 Messages 格式。它不依赖你理解 Responses 的细节；也不是进入编码工具章节的前提。

先在教程仓库根目录导出本章，再进入练习目录：

```bash
uv run python scripts/check_tutorial.py --chapter 05 --export /tmp/minicode-ch05
cd /tmp/minicode-ch05
uv sync
```

导出目标须为空或尚不存在。以下命令在练习目录执行；真实调用沿用第 00 章的环境变量配置。

## 1. 先看到一次真实工具往返

```bash
uv run python examples/05_anthropic.py
```

接口地址需支持 Anthropic Messages。沿用已配置的模型、地址和密钥；如果使用不同服务，先修改这组配置。程序应先打印 `本地 add → 5`，再打印模型根据工具结果给出的回答。

打开 [anthropic_prototype.py 中的 anthropic_roundtrip](../lessons/anthropic_prototype.py)。这一版直接发送两次 HTTP 请求：

```text
user: 请计算
assistant: tool_use
Python: 执行 add
user: tool_result
assistant: 回答
```

工具仍是普通 Python 函数，变化只有请求和结果的外部表示。

## 2. 写出请求、调用与结果

先构造请求。Messages 的工具 schema 字段叫 `input_schema`：

```python
headers = {"x-api-key": config.api_key, "anthropic-version": "2023-06-01"}
body = {
    "model": config.model,
    "max_tokens": config.max_tokens,
    "messages": [{"role": "user", "content": "请调用 add 计算 2 加 3，再回答。"}],
    "tools": [{"name": "add", "description": "两个整数相加", "input_schema": PARAMETERS}],
}
first = post(client, config, "/messages", headers, body)
```

第一次响应中的调用长这样：

```json
{"type": "tool_use", "id": "call_1", "name": "add", "input": {"a": 2, "b": 3}}
```

`input` 已经是对象，可以直接取参数，不需要 `json.loads()`。执行后，把原助手内容与工具结果放回历史：

```python
body["messages"].append({"role": "assistant", "content": first["content"]})
results = []
for block in first["content"]:
    if block["type"] == "tool_use":
        result = str(block["input"]["a"] + block["input"]["b"])
        results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})
body["messages"].append({"role": "user", "content": results})
final = post(client, config, "/messages", headers, body)
```

这是正常路径的解释片段；完整函数会先确认 `stop_reason="tool_use"`。原型保留全部 assistant 内容，因此模型返回的其他必要块也能随历史继续传递。

现在只需记住：`tool_use.id` 对应结果中的 `tool_use_id`，结果装在 **user 消息**中。它并不表示用户亲手输入了这段文字，而是此协议要求的结果位置。

## 3. 从原型提取适配器

把外部内容块转换成已有内部对象：

| Messages 数据 | 内部对象 |
| --- | --- |
| text 块 | `Text` |
| tool_use 块 | `ToolCall` |
| user 中的 tool_result 块 | `ToolResult` |
| end_turn / tool_use | `stop` / `tool_calls` |

完整 [Anthropic 适配器](../lessons/stages/05/minicode/providers/anthropic.py)还把内部 system 消息提到请求顶层的 `system`，并在[工厂](../lessons/stages/05/minicode/providers/__init__.py)注册协议。

```bash
uv run python examples/05_anthropic.py --step enhanced
```

这一检查点用完整适配器接回已有 Agent。Agent 依然只知道统一消息和工具；协议层负责 `user.tool_result` 的转换。

## 4. 再从一次调用扩展到一批调用

现在让模型一次发出两个 `tool_use`。程序逐个执行，再将两个结果放进同一条 user 消息：

```json
{"role": "user", "content": [
  {"type": "tool_result", "tool_use_id": "call_a", "content": "5"},
  {"type": "tool_result", "tool_use_id": "call_b", "content": "9"}
]}
```

它们共同回答上一条 assistant 消息中的调用。完整适配器将内部连续的 tool 消息合并，调用与结果之间不插入普通对话。

再令其中一个工具失败，给它的结果增加 `is_error: true`。另一项仍保留自己的结果与 ID。先跑通一条调用，才能看清批量只是把这一步重复多次。

## 5. 补齐停止状态和原始内容

- `max_tokens` 表示回复被截断，不能继续执行里面的工具；拒绝和未支持状态也需要作为非成功状态返回。
- 思考块和签名随原始 assistant 内容保存；统一结构中放进 `provider_data`。同一路由未编辑消息可以回放，编辑或切换模型后再按第三章规则处理。
- 输入用量可能包含不同缓存类别。先记录用量；精确费用留到第 15、18 章，不在第一次协议往返时加入计价逻辑。

增强版完整支持上述规则。短原型用来观察正常闭环，只处理本地加法工具和两次请求。

## 6. 验收

```bash
uv run python examples/05_anthropic.py --offline
uv run python examples/05_anthropic.py --step enhanced --offline
uv run python -m pytest tests/test_ch05.py -q
```

第一条使用固定 HTTP 响应检查最小原型，第二条检查完整适配器与 Agent。确认第二次请求中 `tool_use_id` 与原调用相同，再检查多个结果会一起回传。

本章复用 [anthropic_prototype.py](../lessons/anthropic_prototype.py)和[加法工具](../lessons/protocol_roundtrip.py)，增加[实验入口](../examples/05_anthropic.py)及当前协议适配器；累计文件见[阶段清单](../lessons/stages.json)。

协议资料：[Anthropic 工具调用与结果](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls)。下一步回到[编码工具](06-coding-tools.md)，让 Agent 修改一个真实文件。
