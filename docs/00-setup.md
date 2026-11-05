# 第 00 章：从空目录发出第一次真实请求

[目录](README.md) · 下一章：[Agent 循环](01-agent-loop.md)

这一章不需要教程仓库里的任何 Python 文件。我们在自己的空目录里写程序，发送一个真实问题，再把模型回复打印出来。需要 Python 3.12+、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和一个支持 Chat Completions 与函数工具的模型账号。

## 1. 建立自己的项目

在你准备存放练习的位置执行：

```bash
mkdir my-agent
cd my-agent
```

新建 `pyproject.toml`，写入：

```toml file=pyproject.toml step=minimal
[project]
name = "my-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["httpx>=0.28,<1"]
```

运行 `uv sync` 安装 HTTP 库。后续所有学习命令都在这个 `my-agent` 目录中执行。

在当前终端配置同一服务的模型、API 根地址和密钥：

```bash
export MINICODE_MODEL="填写支持 Chat Completions 和函数工具的模型 ID"
export MINICODE_BASE_URL="https://api.openai.com/v1"
export MINICODE_API_KEY="填写你的密钥"
```

兼容服务填写自己的根地址，不包含 `/chat/completions`。密钥只放环境变量，真实请求会产生服务商费用。程序不自动读取 `.env`。

## 2. 写完整的第一次请求

新建 `demo.py`，把下面的完整代码写进去：

```python file=demo.py step=minimal
import os
import httpx

model = os.environ["MINICODE_MODEL"]
base_url = os.environ["MINICODE_BASE_URL"].rstrip("/")
api_key = os.environ["MINICODE_API_KEY"]
messages = [{"role": "user", "content": "你好！请用一句话介绍你自己。"}]

with httpx.Client(timeout=60) as client:
    response = client.post(
        base_url + "/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": messages},
    )
    response.raise_for_status()
    reply = response.json()["choices"][0]
    print(reply["message"]["content"])
```

运行你刚写的文件：

```bash
uv run python demo.py
```

先改变问题再运行一次。`messages` 是输入，`client.post()` 发出 HTTP 请求，`choices[0].message.content` 是模型生成的回答。此时先把这三处对应起来。

## 3. 把请求代码提取为自己写的客户端

接下来要发送第二个问题。先将重复的请求代码放入一个新文件 `model.py`。下面给出完整定义；下一章的 `from model import open_model` 就是导入你在这里写的文件。

```python file=model.py step=enhanced
from contextlib import contextmanager
import os
import httpx


class ChatModel:
    def __init__(self, model, base_url, api_key, client=None):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client if client is not None else httpx.Client(timeout=60)
        self.owns_client = client is None

    def complete(self, messages, tools=()):
        body = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = list(tools)
        response = self.client.post(
            self.base_url + "/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=body,
        )
        response.raise_for_status()
        return response.json()["choices"][0]

    def close(self):
        if self.owns_client:
            self.client.close()


@contextmanager
def open_model():
    prefix = "MINICODE"
    if not any(os.environ.get(f"MINICODE_{key}") for key in ("MODEL", "BASE_URL", "API_KEY")):
        prefix = "LLM"
    model = ChatModel(
        os.environ[f"{prefix}_MODEL"],
        os.environ[f"{prefix}_BASE_URL"],
        os.environ[f"{prefix}_API_KEY"],
    )
    try:
        yield model
    finally:
        model.close()
```

`ChatModel.complete()` 保留刚才的请求过程；`tools` 先留空，下一章传入工具声明。`open_model()` 读取配置，建立客户端，在 `with` 结束时关闭连接。`yield model` 把这个客户端交给 `with ... as model` 使用。

配置优先读取完整的 `MINICODE_*` 组；如果这一组没有设置，才读取已有的 `LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY`。不会用另一组密钥补全半组配置。

现在把 `demo.py` 全部替换为：

```python file=demo.py step=enhanced
from model import open_model

with open_model() as model:
    messages = [{"role": "user", "content": "我叫小林，请记住我的名字。"}]
    first = model.complete(messages)
    messages.append(first["message"])
    messages.append({"role": "user", "content": "我叫什么名字？"})
    second = model.complete(messages)
    print(second["message"]["content"])
```

再次运行 `uv run python demo.py`。第二次请求带上了 `user → assistant → user` 三条消息，所以模型能看到你之前说的名字。

## 4. 验收与下一步

检查你自己的目录应有：

```text
my-agent/
  pyproject.toml
  model.py
  demo.py
```

关闭终端再运行会缺少环境变量，需在新终端重新设置。401/403 先核对密钥，404 先核对地址、模型与协议。运行成功后，试着不把第一条用户消息加入第二次请求，观察回答变化。

正文已经包含全部需要手写的内容。教程仓库的阶段导出与离线检查只是维护工具，见[目录中的验收说明](README.md#验证正文确实能从零运行)。协议字段见 [Chat Completions 官方接口](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)。
