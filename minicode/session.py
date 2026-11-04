"""用内部消息保存会话；每次替换都是完整快照，不自动重放工具。"""
import json
import os
import tempfile
from pathlib import Path

from .types import Message, ToolResult, message_from_dict, message_to_dict, tool_calls

VERSION = 1
INTERRUPTED = "调用被中断，结果未知。请先检查现状，不要假定操作未发生，也不要直接重做。"


def repair_pending(messages: list[Message]) -> list[Message]:
    """在原来的调用后补齐缺失结果；绝不执行调用。"""
    repaired = []
    index = 0
    seen_calls = set()
    while index < len(messages):
        message = messages[index]
        if message.role == "tool":
            raise ValueError("会话包含没有相邻工具调用的结果")
        repaired.append(message)
        index += 1
        calls = tool_calls(message)
        if not calls:
            continue
        expected = {call.id for call in calls}
        if len(expected) != len(calls) or expected & seen_calls:
            raise ValueError("会话包含重复工具调用 ID")
        seen_calls.update(expected)
        answered = set()
        while index < len(messages) and messages[index].role == "tool":
            result_message = messages[index]
            for part in result_message.content:
                if not isinstance(part, ToolResult) or part.call_id not in expected:
                    raise ValueError("工具结果没有对应调用")
                if part.call_id in answered:
                    raise ValueError("同一个工具调用出现了重复结果")
                answered.add(part.call_id)
            repaired.append(result_message)
            index += 1
        missing = [ToolResult(call.id, INTERRUPTED, True) for call in calls if call.id not in answered]
        if missing:
            repaired.append(Message("tool", missing))
    return repaired


class SessionStore:
    def __init__(self, path: str | Path, *, source: dict | None = None):
        self.path = Path(path)
        self.source = {key: value for key, value in (source or {}).items()
                       if key in {"api", "model", "provider", "base_url"}}

    def save(self, history: list[Message]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        header = {"type": "session", "version": VERSION, "source": self.source}
        records = [header] + [{"type": "message", "message": message_to_dict(m)} for m in history]
        # 临时文件和目标在同一个目录，replace 不会暴露写到一半的历史。
        fd, temporary = tempfile.mkstemp(prefix=".minicode-session-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                for record in records:
                    output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def load(self, *, repair: bool = True) -> list[Message]:
        if not self.path.exists():
            return []
        try:
            records = [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines()]
            if not records or records[0].get("type") != "session" or records[0].get("version") != VERSION:
                raise ValueError("不支持的会话版本")
            history = []
            for record in records[1:]:
                if record.get("type") != "message":
                    raise ValueError("未知会话记录类型")
                history.append(message_from_dict(record["message"]))
        except (KeyError, TypeError, AttributeError, json.JSONDecodeError) as error:
            raise ValueError(f"会话文件损坏：{self.path}") from error
        return repair_pending(history) if repair else history


def run_session(agent, prompt: str, store: SessionStore):
    events = agent.run(prompt)
    try:
        for event in events:
            if event["type"] not in {"text_delta", "tool_call_delta"}:
                store.save(agent.history)
            yield event
    finally:
        events.close()
        store.save(agent.history)
