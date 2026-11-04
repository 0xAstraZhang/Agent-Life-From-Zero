"""第 03 章：统一类型、用量和错误；流式与恢复分别在 08、10 章加入。"""
from dataclasses import asdict
from .types import Message, ModelReply, ToolResult, Usage, message_text, tool_calls


class Agent:
    def __init__(self, model, runner, system_prompt="你是一个编程助手。", *, max_turns=20):
        if max_turns < 1:
            raise ValueError("max_turns 必须至少为 1")
        self.model, self.runner, self.max_turns = model, runner, max_turns
        self.history = [Message.text("system", system_prompt)]
        self.usage = Usage()

    def run(self, prompt):
        self.history.append(Message.text("user", prompt))
        for turn in range(1, self.max_turns + 1):
            try:
                reply = self.model.complete(self.history, self.runner.specs())
                if not isinstance(reply, ModelReply) or reply.message.role != "assistant":
                    raise ValueError("模型必须返回 assistant ModelReply")
            except Exception as error:
                yield self._result("error", f"模型请求失败：{error}", turn)
                return
            self.usage.input_tokens += reply.usage.input_tokens
            self.usage.output_tokens += reply.usage.output_tokens
            text = message_text(reply.message)
            calls = tool_calls(reply.message)
            if reply.stop_reason not in {"stop", "tool_calls"}:
                if text:
                    self.history.append(Message.text("assistant", text))
                status = reply.stop_reason if reply.stop_reason in {"length", "refusal", "cancelled"} else "error"
                yield self._result(status, reply.error or text, turn)
                return
            if bool(calls) != (reply.stop_reason == "tool_calls"):
                yield self._result("error", "停止原因与调用不一致", turn)
                return
            prior_ids = {c.id for message in self.history for c in tool_calls(message)}
            ids = [c.id for c in calls]
            if any(not value for value in ids) or len(set(ids)) != len(ids) or prior_ids.intersection(ids):
                yield self._result("error", "工具调用 ID 缺失或重复", turn)
                return
            self.history.append(reply.message)
            yield {"type": "assistant", "message": reply.message, "text": text, "tool_calls": calls}
            if reply.stop_reason == "stop":
                yield self._result("success", text, turn)
                return
            for request in calls:
                result = self.runner.run(request)
                self.history.append(Message("tool", [result]))
                yield {"type": "tool_result", "result": result, "call_id": result.call_id,
                       "content": result.content, "is_error": result.is_error}
        yield self._result("max_turns", "已达到最大轮数", self.max_turns)

    def _result(self, status, text, turns):
        return {"type": "result", "status": status, "text": text,
                "turns": turns, "usage": asdict(self.usage)}
