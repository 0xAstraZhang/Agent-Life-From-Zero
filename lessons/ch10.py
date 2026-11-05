"""第 10 章两个独立原型：完整历史落盘；为下一次请求制作摘要。"""
from dataclasses import asdict
import json

from minicode.types import Message, Text, ToolCall, ToolResult, message_text


def save_history(path, history):
    records = [asdict(message) for message in history]
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


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
