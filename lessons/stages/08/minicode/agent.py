"""只编排内部消息与工具；不认识任何服务商的请求字段。"""
from dataclasses import asdict

from .types import Message, ModelReply, ToolResult, Usage, message_text, tool_calls


class Agent:
    def __init__(self, model, runner, system_prompt="你是一个编程助手。", *,
                 max_turns=20, stream=False):
        if max_turns < 1:
            raise ValueError("max_turns 必须至少为 1")
        self.model, self.runner = model, runner
        self.max_turns, self.stream = max_turns, stream
        self.history = [Message.text("system", system_prompt)]
        self.usage = Usage()

    def run(self, prompt: str):
        """一个用户任务，产出事件，正常消费时以唯一的 result 事件结束。"""
        self.repair_pending()
        self.history.append(Message.text("user", prompt))
        turn = 0
        try:
            for turn in range(1, self.max_turns + 1):
                try:
                    reply = yield from self._request()
                    if not isinstance(reply, ModelReply) or reply.message.role != "assistant":
                        raise ValueError("模型必须返回 assistant ModelReply")
                except Exception as error:
                    yield self._result("error", f"模型请求失败：{type(error).__name__}: {error}", turn)
                    return
                self.usage.input_tokens += reply.usage.input_tokens
                self.usage.output_tokens += reply.usage.output_tokens
                text = message_text(reply.message)
                calls = tool_calls(reply.message)
                if reply.stop_reason not in {"stop", "tool_calls"}:
                    # 不回放截断的调用或原始协议数据，也不执行看起来已经完整的参数。
                    if text:
                        self.history.append(Message.text("assistant", text))
                    yield {"type": "assistant", "message": Message.text("assistant", text),
                           "text": text, "tool_calls": []}
                    status = reply.stop_reason if reply.stop_reason in {"length", "refusal", "cancelled"} else "error"
                    yield self._result(status, reply.error or text or f"模型停止：{reply.stop_reason}", turn)
                    return
                if bool(calls) != (reply.stop_reason == "tool_calls"):
                    yield self._result("error", "模型停止原因与工具调用不一致", turn)
                    return
                prior_ids = {call.id for message in self.history for call in tool_calls(message)}
                ids = [call.id for call in calls]
                if any(not value for value in ids) or len(set(ids)) != len(ids) or prior_ids.intersection(ids):
                    yield self._result("error", "模型返回了缺失或重复的工具调用 ID", turn)
                    return
                self.history.append(reply.message)
                yield {"type": "assistant", "message": reply.message, "text": text, "tool_calls": calls}
                if reply.stop_reason == "stop":
                    yield self._result("success", text, turn)
                    return
                for call in calls:
                    try:
                        result = self.runner.run(call)
                    except Exception as error:
                        result = ToolResult(call.id, f"工具执行层失败：{type(error).__name__}: {error}", True)
                    self.history.append(Message("tool", [result]))
                    yield self._tool_event(result)
            yield self._result("max_turns", f"已达到最大轮数 {self.max_turns}", turn)
        except KeyboardInterrupt:
            for result in self.repair_pending():
                yield self._tool_event(result)
            yield self._result("cancelled", "用户中断了任务", turn)
        finally:
            # 消费者 close() 或 Ctrl+C 都不能留下没有结果的调用。
            self.repair_pending()

    def _request(self):
        if not self.stream:
            return self.model.complete(self.history, self.runner.specs())
        reply = None
        stream = iter(self.model.stream(self.history, self.runner.specs()))
        try:
            for event in stream:
                if reply is not None:
                    raise ValueError("完成事件之后仍收到流数据")
                if event.type == "done":
                    if event.reply is None:
                        raise ValueError("完成事件缺少最终回复")
                    reply = event.reply
                elif event.type == "text_delta":
                    yield {"type": "text_delta", "text": event.text}
                elif event.type == "tool_call_delta":
                    # 参数分片仅供观察；执行必须等到最终回复通过检查。
                    yield {"type": "tool_call_delta", "call_id": event.call_id,
                           "arguments_delta": event.arguments_delta}
                else:
                    raise ValueError(f"未知流事件：{event.type}")
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        if reply is None:
            raise ValueError("流在完成事件之前结束")
        return reply

    def repair_pending(self, note="调用被中断，结果未知。请先检查现状，不要假定操作没有发生。"):
        answered = {part.call_id for message in self.history for part in message.content
                    if isinstance(part, ToolResult)}
        results = []
        for message in list(self.history):
            for call in tool_calls(message):
                if call.id not in answered:
                    result = ToolResult(call.id, note, True)
                    self.history.append(Message("tool", [result]))
                    answered.add(call.id)
                    results.append(result)
        return results

    @staticmethod
    def _tool_event(result):
        return {"type": "tool_result", "result": result, "call_id": result.call_id,
                "content": result.content, "is_error": result.is_error}

    def _result(self, status, text, turns):
        return {"type": "result", "status": status, "text": text,
                "turns": turns, "usage": asdict(self.usage)}
