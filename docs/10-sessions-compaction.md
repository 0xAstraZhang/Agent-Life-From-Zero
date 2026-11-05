# 第 10 章：先保存对话，再为长对话制作摘要

[上一章：上下文](09-context.md) · [目录](README.md) · [下一章：Hooks 与规划](11-hooks-planning.md)

这一章做两个独立实验。第一个让新建的 Agent 继续之前的对话；第二个把旧对话变成较短的请求。先分别跑通，再组合。

## 1. 最小会话：保存 → 读取 → 继续

`Agent.history` 已经有完整消息。保存会话就是把这些消息写入文件，恢复就是读回它们，交给新的 Agent。

先把数据类转成普通字典，再写成 JSON。以下是 [lessons/ch10.py](../lessons/ch10.py) 中的完整保存函数：

```python
def save_history(path, history):
    records = [asdict(message) for message in history]
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
```

文件读回后，把每个内容块重建为对应的数据类。下面处理正常记录；损坏文件的完整校验在增强步骤中加入：

```python
def load_history(path):
    records = json.loads(path.read_text(encoding="utf-8"))
    history = []
    for record in records:
        parts = []
        for saved in record["content"]:
            part = dict(saved)
            kind = part.pop("type")
            if kind == "text":
                parts.append(Text(**part))
            elif kind == "tool_call":
                parts.append(ToolCall(**part))
            elif kind == "tool_result":
                parts.append(ToolResult(**part))
            else:
                raise ValueError(f"未知内容块：{kind}")
        history.append(Message(record["role"], parts, record.get("provider_data", {})))
    return history
```

两个函数使用 `json`、`asdict`，以及前章的 `Message`、`Text`、`ToolCall`、`ToolResult`，完整导入在原型文件开头。调用和结果都保留 ID，`provider_data` 也原样保存。

恢复入口只需给 Agent 构造函数增加 `history=None`，并将历史初始化改为：

```python
self.history = list(history) if history is not None else [Message.text("system", system_prompt)]
```

完整阶段版本见 [agent.py](../lessons/stages/10/minicode/agent.py)。随后就能写出下面的顺序：

```python
save_history(path, agent.history)
history = load_history(path)
resumed = Agent(model, runner, history=history)
for event in resumed.run("项目代号和测试命令分别是什么？"):
    print(event)
```

`minimal` 示例先告诉模型项目代号和测试命令，保存到磁盘，删除第一个 Agent，再用读回的历史继续。新问题没有重述答案；观察第二次请求能否沿用已保存的信息。

## 运行本章检查点

沿用第 00 章配置的 `MINICODE_MODEL`、`MINICODE_BASE_URL`、`MINICODE_API_KEY`。默认使用真实 API；回答措辞不固定，观察消息与工具的实际变化。需要独立工作目录时，在教程仓库根目录导出：

```bash
uv run python scripts/check_tutorial.py --chapter 10 --export /tmp/minicode-ch10
cd /tmp/minicode-ch10
uv sync
```

在仓库根目录或导出目录运行：

```bash
uv run python examples/10_sessions.py --step minimal
uv run python examples/10_sessions.py --step enhanced
uv run python examples/10_sessions.py --step compact
uv run python examples/10_sessions.py --step compact-enhanced
```

给同一命令加 `--offline` 可运行固定回复的回归检查。它用来验证程序控制流；学习时先观察真实请求。协议可用 `--api` 选择。


## 2. 会话增强：处理保存和恢复中途失败

正常保存恢复已经可用，再替换为 [SessionStore](../minicode/session.py)：

```python
store = SessionStore(".minicode/session.jsonl")
store.save(agent.history)
resumed = Agent(model, runner, history=store.load())
for event in run_session(resumed, "继续任务", store):
    print(event)
```

`enhanced` 只演示保存恢复，不启用压缩。增强实现增加 JSONL 格式版本、内容块校验、临时文件写完再替换，以及事件发生后的自动保存。序列化完整定义见 [本章 types.py](../lessons/stages/10/minicode/types.py)。

还有一种失败需要单独理解：工具已经写入文件，但进程在保存结果前退出。恢复时只知道“有调用、没结果”，不能自动重做。`repair_pending()` 补一条“结果未知”的工具结果，让模型先检查现状。

## 3. 最小压缩：为下一次请求制作一个视图

现在暂时放下磁盘文件。长历史需要缩短时，取出旧的完整用户轮，让模型写摘要，再拼回 system 和最新用户轮：

```text
完整历史：system | 旧用户轮及其工具往返 | 最新用户轮
模型请求：system | 旧轮摘要             | 最新用户轮
```

以下是完整的压缩原型；它在新用户轮开始时调用，不修改传入的 `history`：

```python
def compact_history(model, history):
    """在新用户轮开始时调用：保留 system 和最新用户轮，摘要此前完整轮次。"""
    latest_user = max(index for index, message in enumerate(history) if message.role == "user")
    systems = [message for message in history[:latest_user] if message.role == "system"]
    old_turns = [message for message in history[:latest_user] if message.role != "system"]
    transcript = json.dumps([asdict(message) for message in old_turns], ensure_ascii=False)
    reply = model.complete([
        Message.text("system", "把此前对话写成简短交接摘要。保留目标、约束、已知结果和未完成事项。"),
        Message.text("user", transcript),
    ], [])
    summary = message_text(reply.message).strip()
    if reply.stop_reason != "stop" or not summary:
        raise RuntimeError("摘要没有完成")
    return systems + [Message.text("user", "此前任务摘要：\n" + summary)] + history[latest_user:]
```

`compact` 检查点先完成一轮真实对话，然后独立发起摘要请求，打印拼好的请求视图，最后用这个视图回答新问题。它不读写会话文件。观察摘要保留了什么，也比较压缩前后的文本；短对话不一定会变短。

这里按用户轮切分，而不是取最后若干条消息，因此旧轮的工具调用和结果一起进入摘要。最新问题保持原文，system 保持原有角色。

## 4. 压缩增强：何时摘要，失败怎么办

理解请求视图后，再引入 [CompactingModel](../lessons/stages/10/minicode/compact.py)：

```python
model = CompactingModel(raw_model, threshold_chars=48_000, keep_turns=2)
agent = Agent(model, runner, history=history)
```

`compact-enhanced` 用很低的阈值演示自动触发。增强层负责字符阈值、保留近期轮次、摘要缓存、失败回退和摘要用量统计。它包装模型，不覆盖完整会话。

字符阈值用于估计请求大小。单轮工具输出很长时，还需要限制或分段读取工具输出；第 18 章再处理长任务运行控制。

## 检查与练习

```bash
uv run python examples/10_sessions.py --step minimal --offline
uv run python examples/10_sessions.py --step enhanced --offline
uv run python examples/10_sessions.py --step compact --offline
uv run python examples/10_sessions.py --step compact-enhanced --offline
uv run python -m pytest tests/test_lesson_10.py tests/test_session.py tests/test_compact.py -q
```

先验证保存后的工具 ID 和原始续轮数据能读回，摘要后的最新问题和完整历史不变，再检查损坏文件、中断记录和摘要失败回退。

练习：在第一次对话中加入另一个项目约定，分别经过磁盘恢复和摘要后询问它，比较两条路径保留的信息。会话含有实际对话和工具结果，保存目录放进 `.gitignore`。
