# 第 00 章：环境准备，先看清一次模型交互

[目录](README.md) · 下一章：[最小 Agent Loop](01-agent-loop.md)

## 这一章的结果

你会运行一个不联网的小程序，看见一条用户消息变成一条模型回复。先认识消息和请求，再在下一章让程序执行模型提出的工具调用。

本章只需一个 Python 文件，依赖和环境由仓库配置提供：

- [项目依赖](../pyproject.toml)：Python 版本、运行依赖和测试依赖。
- [第 00 章实验](../examples/00_hello.py)：一次请求和回复。

## 第一步：安装环境

需要 Python 3.12 或更高版本。使用 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装 uv，然后在仓库根目录执行：

```bash
uv sync --locked
uv run python --version
```

第一条命令按 `uv.lock` 安装依赖，并把当前项目安装为可编辑包。这样修改 `minicode/` 后，示例立刻使用新代码，不用每改一次就重新安装。

依赖各做一件事：

| 依赖 | 用在哪里 |
| --- | --- |
| `httpx` | 第 03 章开始发送 HTTP 请求、读取 SSE |
| `jsonschema` | 第 02 章验证工具参数 |
| `pytest` | 从第一章开始验证确定的控制流 |

我们直接实现 API 的请求和响应转换。HTTP 库只处理传输，不替我们决定消息是什么、工具什么时候执行。

## 第二步：运行第一次交互

```bash
uv run python examples/00_hello.py
```

预期输出：

```text
你好，我是 minicode。
停止原因： stop
模型请求次数： 1
```

在空目录中创建 `hello.py`，写入下面的完整代码。它不导入后续章节的任何文件：

```python
from copy import deepcopy


class ScriptedModel:
    def __init__(self):
        self.requests = []

    def complete(self, messages):
        self.requests.append(deepcopy(messages))
        return {"text": "你好，我是 minicode。", "stop_reason": "stop"}


model = ScriptedModel()
reply = model.complete([{"role": "user", "text": "你好"}])
print(reply["text"])
print("停止原因：", reply["stop_reason"])
print("模型请求次数：", len(model.requests))
```

用 `python hello.py` 运行手写版本。这里先用普通字典表达消息，`complete()` 接收消息列表并返回文本和停止原因。下一章才把这些数据改成明确的类型，真实接口加入时再记录用量。

`ScriptedModel` 只返回固定文本，不理解输入，也不访问网络。下一章的 `FakeModel` 会进一步支持一段多轮剧本，用来检查 Agent 的控制流。

## 第三步：理解三个对象

一开始只需要三个概念：

1. **消息**：谁说了什么。用户输入和助手输出都放进历史。
2. **模型客户端**：接收历史，返回下一条助手消息。
3. **停止原因**：这次输出结束，是正常完成、要调用工具，还是被截断？

一次普通聊天的历史是：

```text
user: 你好
assistant: 你好，我是 minicode。
```

下一章加入工具之后，会变成：

```text
user: 查看文件
assistant: 请求调用 Read
tool: 文件内容
assistant: 根据文件内容回答
```

模型发出请求，Python 程序执行工具。模型返回了一段参数，不意味着对应操作已经发生。

## 第四步：先修改实验，再接真实服务

把假模型的回复改成另一句话，再运行程序。接着打印 `model.requests`，观察模型收到的是一个消息列表。

在第一章，你会写消息类型和最小循环。到第三章再设置真实 API。这个顺序让循环的错误可以离线复现，不需要靠反复花钱试提示词来定位。

真实服务配置使用以下环境变量：

| 变量 | 含义 |
| --- | --- |
| `MINICODE_API` | 三种协议之一 |
| `MINICODE_MODEL` | 服务商实际提供的模型 ID |
| `MINICODE_BASE_URL` | API 根地址，例如以 `/v1` 结尾的地址 |
| `MINICODE_PROVIDER` | 来源名称，用于续轮数据来源标记 |
| `MINICODE_API_KEY` | 密钥，程序不会把它放进会话 |

不自动读取 `.env`，也不在代码中保存密钥。具体协议配置在第 03–05 章给出。

## 验收与排错

现在只要求第 00 章实验运行成功。还可以从仓库导出本阶段的完整工作目录：

```bash
uv run python scripts/check_tutorial.py --chapter 0 --export /tmp/minicode-ch00
```

目标必须为空目录或尚不存在；导出文件中的 README 给出当前阶段的安装和运行命令。默认验收会移除仓库导入路径，并禁止导入本阶段尚未出现的包。

| 现象 | 检查 |
| --- | --- |
| `uv: command not found` | 安装 uv，并重新打开终端 |
| Python 版本不满足要求 | 用 uv 安装或选择 Python 3.12+ |
| `No module named minicode` | 确认在仓库根目录运行过 `uv sync --locked` |
| 示例找不到 | 当前目录应同时包含 `docs/`、`examples/`、`pyproject.toml` |
| 锁文件不一致 | 不手改 `uv.lock`；修改依赖后由维护者运行 `uv lock` |

## 练习

- 把输入改为两个消息，观察假模型记录的请求。
- 写下“模型说要读文件”和“程序读到了文件”之间还缺哪一步。
- 解释为什么 `stop` 和输出长度达到上限不能当作同一种结束。

下一章把这些对象串成一个能调用工具的循环。
