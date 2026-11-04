"""按剧本回答的模型：测试运行时，不需要网络或 API Key。"""
import copy
from itertools import count

from .types import Message, ModelReply, StreamEvent, Text, ToolCall, Usage, message_text

_ids = count(1)


def call(name: str, /, **args) -> ToolCall:
    return ToolCall(f"fake_{next(_ids)}", name, args)


class FakeModel:
    def __init__(self, script):
        self.script = list(script)
        self.requests: list[dict] = []

    def complete(self, messages, tools=()) -> ModelReply:
        self.requests.append(copy.deepcopy({"messages": messages, "tools": list(tools)}))
        if not self.script:
            raise RuntimeError("FakeModel 剧本已用完；请补充预期回复")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, ModelReply):
            return copy.deepcopy(step)
        if isinstance(step, str):
            return ModelReply(Message.text("assistant", step), Usage(), "stop")
        calls = [step] if isinstance(step, ToolCall) else step
        if not isinstance(calls, list) or not calls or not all(isinstance(c, ToolCall) for c in calls):
            raise TypeError("FakeModel 剧本项必须是文本、工具调用、ModelReply 或异常")
        return ModelReply(Message("assistant", copy.deepcopy(calls)), Usage(), "tool_calls")

    def stream(self, messages, tools=()):
        reply = self.complete(messages, tools)
        for char in message_text(reply.message):
            yield StreamEvent("text_delta", text=char)
        yield StreamEvent("done", reply=reply)
