# 第 00 章：让 Python 真正和模型说一句话

[目录](README.md) · 下一章：[最小 Agent Loop](01-agent-loop.md)

这一章先做一件能看见结果的事：在 Python 中发出问题，打印模型的真实回答。然后再给它发第二句话，观察历史消息如何让对话连起来。

## 1. 准备环境和模型

需要 Python 3.12+、[uv](https://docs.astral.sh/uv/getting-started/installation/)，以及一个支持 **Chat Completions 和函数工具调用**的模型账号。后两章会沿用同一模型；模型 ID 请填写服务商实际提供的值。

在仓库根目录安装依赖：

```bash
uv sync --locked
```

在同一个终端设置配置。`BASE_URL` 是 API 根地址，程序会在末尾加 `/chat/completions`：

```bash
export MINICODE_API="openai-chat"
export MINICODE_BASE_URL="https://api.openai.com/v1"
export MINICODE_MODEL="填写你账号可用的模型 ID"
```

另行通过环境变量设置 `MINICODE_API_KEY`。密钥不写进 Python 文件或仓库；程序不会自动读取 `.env`。真实请求会产生服务商费用。已有完整的 `LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY` 时，也可以继续用这一组；不要同时留下半组 `MINICODE_*` 配置。

本章代码是 [lessons/ch00.py](../lessons/ch00.py)，运行入口是 [examples/00_hello.py](../examples/00_hello.py)。想在独立目录手写，可先导出本章：

```bash
uv run python scripts/check_tutorial.py --chapter 0 --export /tmp/minicode-ch00
cd /tmp/minicode-ch00
uv sync
```

导出目标须为空。接下来的命令既可在仓库根目录运行，也可在这个练习目录运行。

## 2. 最小原型：一次请求，一次回答

```bash
uv run python examples/00_hello.py --step minimal
```

你会收到模型生成的一句话。改动提示词后再次运行，回答也会改变；这里没有预先写好的答案。

请求最重要的内容只有两个字段：

```python
body = {
    "model": "你选择的模型 ID",
    "messages": [{"role": "user", "content": "你好！"}],
}
```

`model` 决定请求哪个模型；`messages` 记录谁说了什么。通过 HTTP 发出去，再从返回值中取出助手消息：

```python
response = client.post(base_url + "/chat/completions", json=body)
response.raise_for_status()
reply = response.json()["choices"][0]
print(reply["message"]["content"])
```

`client` 是带认证头的 `httpx.Client`。完整文件把这几行放进 `ChatModel.complete()`，把环境变量和连接的建立、关闭放进 `open_chat()`。它们不决定模型回答什么。

下面是可以直接复制运行的检查点。`open_chat()` 默认连接刚才配置的真实 API：

```python
# 可运行检查点：00-minimal
from lessons.ch00 import open_chat

with open_chat() as model:
    messages = [{"role": "user", "content": "你好！请用一句话介绍你自己。"}]
    reply = model.complete(messages)
    print(reply["message"]["content"])
```

到这里先停下来改一次问题，确认你能解释：输入在哪里、HTTP 请求在哪里、回答从哪个字段取出来。

## 3. 增强一步：让模型记住上一句话

模型不会因为前一次 HTTP 请求结束，就自动记住你的对话。下一次请求要带上之前的用户消息和助手回答：

```python
# 可运行检查点：00-enhanced
from lessons.ch00 import open_chat

with open_chat() as model:
    messages = [{"role": "user", "content": "我叫小林，请记住我的名字。"}]
    first = model.complete(messages)
    messages.append(first["message"])
    messages.append({"role": "user", "content": "我叫什么名字？"})
    second = model.complete(messages)
    print(second["message"]["content"])
```

完整入口中的同一实验：

```bash
uv run python examples/00_hello.py --step enhanced
```

第二次请求的顺序是 `user → assistant → user`。下一章只会在这份历史里再增加“工具调用”和“工具结果”。

## 4. 运行成功以后，再看失败和检查

| 现象 | 从哪里检查 |
| --- | --- |
| 缺少环境变量 | 同一组 `MODEL`、`BASE_URL`、`API_KEY` 是否齐全 |
| HTTP 401/403 | 密钥是否有效、账号是否有访问权限 |
| HTTP 404 或模型不存在 | 根地址、模型 ID、协议是否匹配 |
| `uv` 不存在 | 按 uv 安装说明安装后重新打开终端 |

自动检查使用明确的离线开关；它是测试手段，不是学习前置步骤：

```bash
uv run python examples/00_hello.py --offline --step minimal
uv run python examples/00_hello.py --offline --step enhanced
uv run python -m pytest tests/test_ch00.py -q
```

测试会检查实际发出的请求体，以及第二次请求是否包含完整历史。离线回复固定，不能用来评价模型能力。

练习：去掉第二次请求中的第一条用户消息，再问名字；比较两次回答。你已经亲手控制了模型能看到的上下文。

协议字段可查 [Chat Completions 官方接口](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。下一章：[让模型提出动作，让 Python 执行动作](01-agent-loop.md)。
