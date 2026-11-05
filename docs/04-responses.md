# 第 4 章（选修）：把同一个工具接到 Responses

[上一章：Chat Completions](03-chat-completions.md) · [目录](README.md) · [继续主线：编码工具](06-coding-tools.md)

你已经有一个能使用工具的 Agent。这一章只换模型接口，观察同一个“调用 → 执行 → 结果”在 Responses 中怎样表示。如果当前账号使用 Chat Completions，可以先到第 06 章完成文件任务，再回来扩展协议。

先在教程仓库根目录导出本章，再进入练习目录：

```bash
uv run python scripts/check_tutorial.py --chapter 04 --export /tmp/minicode-ch04
cd /tmp/minicode-ch04
uv sync
```

导出目标须为空或尚不存在。以下命令在练习目录执行；真实调用沿用第 00 章的环境变量配置。

## 1. 先跑通两次真实请求

```bash
uv run python examples/04_responses.py
```

程序直接发送 HTTP 请求，执行本地 `add(2, 3)`，再把结果交给模型。应先看到 `本地 add → 5`，然后看到模型的回答。模型回答措辞可以不同；关键是中间确实执行了本地函数。

最小实现是 [responses_prototype.py 中的 responses_roundtrip](../lessons/responses_prototype.py)。它复用前章的连接配置，自己组织两次请求，不经过完整的 Agent 或协议适配器。把这个函数复制到自己的练习文件，先沿下面的数据流读一遍：

```text
input: 用户问题 + tools: add 说明
    ↓ 第一次 POST /responses
output: function_call
    ↓ Python 执行 add
input: 用户问题 + 原 output + function_call_output
    ↓ 第二次 POST /responses
output: 模型回答
```

## 2. 写出第一次请求与结果回传

先给模型说明工具。以下是 `responses_roundtrip()` 中请求体的核心字段；`PARAMETERS` 是第 02 章已有的加法 schema：

```python
body = {
    "model": config.model,
    "input": [{"role": "user", "content": "请调用 add 计算 2 加 3，再回答。"}],
    "tools": [{"type": "function", "name": "add", "description": "两个整数相加",
               "parameters": PARAMETERS, "strict": False}],
    "store": False,
    "include": ["reasoning.encrypted_content"],
}
first = post(client, config, "/responses", headers, body)
```

`post()` 只发送 JSON、检查 HTTP 状态并取回 JSON。工具由下面的 Python 代码执行。先只追踪一次正常 `function_call`：

```python
body["input"].extend(first["output"])
for item in first["output"]:
    if item["type"] == "function_call":
        arguments = json.loads(item["arguments"])
        result = str(arguments["a"] + arguments["b"])
        body["input"].append({
            "type": "function_call_output",
            "call_id": item["call_id"],
            "output": result,
        })
final = post(client, config, "/responses", headers, body)
```

这是完整函数中的正常路径。实际检查点还在执行前确认本轮已经 `completed`，避免把未完成回复当成调用。第一次响应的全部 `output` 原样进入下一次 `input`；当前阶段不需要拆解其中的推理信息。

这里有三个对象：

| 对象 | 作用 |
| --- | --- |
| `message` | 用户或模型的文字 |
| `function_call` | 模型提出调用；`arguments` 是 JSON 字符串 |
| `function_call_output` | Python 返回结果；`call_id` 指向原调用 |

`function_call.id` 标识响应 item，`function_call.call_id` 才用于关联工具结果。例如原调用有 `id="fc_1"`、`call_id="call_1"`，结果就必须带 `call_id="call_1"`。

现在把 `responses_roundtrip()` 的默认参数 `a=2, b=3` 改成 `a=7, b=8`，再运行。观察参数和本地结果都变成了模型本次选择的值。

## 3. 再接回已有 Agent

最小原型固定演示两次请求。要继续处理更多轮调用，把刚才的协议转换提取成 `encode()` 和 `decode()`：

| 内部对象 | Responses 数据 |
| --- | --- |
| `Message` 中的文本 | message item |
| `ToolCall` | function_call，内部 ID 使用 call_id |
| `ToolResult` | function_call_output |
| `ModelReply` | 文本、调用、用量和本轮停止原因 |

完整适配器是 [providers/responses.py](../lessons/stages/04/minicode/providers/responses.py)，在 [工厂](../lessons/stages/04/minicode/providers/__init__.py) 注册 `openai-responses`。Agent 和工具执行器沿用前章。

```bash
uv run python examples/04_responses.py --step enhanced
```

这一次由已有 Agent 管理历史与循环。先前手写的两次请求说明了它内部重复的动作；换适配器不需要重写加法函数。

## 4. 用具体问题补齐边界

**响应完成后还有工具调用。** `status="completed"` 只说明这次响应完整；含 `function_call` 时，Agent 仍需执行并继续。没有调用时才把本轮解释为正常回答。

**响应被截断。** `incomplete` 不能进入工具执行，`max_output_tokens` 对应内部 `length`；失败、取消和拒绝也分别传回状态。试着删除离线数据的一部分或改成 incomplete，检查工具没有运行。

**续轮需要原始 items。** 原型已经保留完整 `output`，`store=False` 配合 `reasoning.encrypted_content` 用于客户端持有历史。不要把加密 reasoning 当作显示文本。统一消息无法表达的原始数据由 `provider_data` 保存。

**用户编辑历史或切换模型。** 增强版才需要来源标记和内容指纹：同来源、内容未改时回放；改变后重新编码公共内容。相关实现沿用第三章的 `remember()` / `replay()`。

**工具失败。** 内部 `ToolResult.is_error` 不变；适配器给文本结果加 `[tool error]` 前缀。这个转换留在协议层。

## 5. 对两个检查点验收

默认命令使用真实 API。`--offline` 才使用固定 HTTP 响应，不需要密钥：

```bash
uv run python examples/04_responses.py --offline
uv run python examples/04_responses.py --step enhanced --offline
uv run python -m pytest tests/test_ch03.py tests/test_ch04.py -q
```

第一条检查直接 HTTP 原型，第二条检查完整适配器和 Agent。看第二次请求：应包含原调用和匹配 `call_id` 的结果。再改变 item 的 `id` 而保留 `call_id`，配对应保持正确。

本章新增最小原型 [responses_prototype.py](../lessons/responses_prototype.py)、增强版共用的[加法工具](../lessons/protocol_roundtrip.py)和[实验入口](../examples/04_responses.py)；完整阶段文件由[阶段清单](../lessons/stages.json)记录。协议映射的短片段用于解释数据流，完整可运行函数以上述文件为准。

协议资料：[Responses API](https://developers.openai.com/api/reference/resources/responses)。完成后可继续[主线第 06 章](06-coding-tools.md)，或选修[Anthropic Messages](05-anthropic.md)。
