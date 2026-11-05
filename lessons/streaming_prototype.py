"""第 08 章：先显示文本，再把完整回复交回工具循环。"""
from minicode.types import Message, tool_calls


def request_stream(model, messages, tools=(), on_text=lambda text: print(text, end="", flush=True)):
    reply = None
    stream = iter(model.stream(messages, tools))
    try:
        for event in stream:
            if reply is not None:
                raise ValueError("完整回复之后又收到流事件")
            if event.type == "text_delta":
                on_text(event.text)
            elif event.type == "done":
                reply = event.reply
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    if reply is None:
        raise ValueError("流已结束，但没有收到完整回复")
    return reply


def stream_roundtrip(model, runner, prompt, on_text=lambda text: print(text, end="", flush=True)):
    history = [Message.text("user", prompt)]
    for _ in range(4):
        reply = request_stream(model, history, runner.specs(), on_text)
        if reply.stop_reason not in {"stop", "tool_calls"}:
            raise ValueError(f"模型回复未正常完成：{reply.stop_reason}")
        calls = tool_calls(reply.message)
        if reply.stop_reason == "tool_calls" and not calls:
            raise ValueError("模型要求执行工具，却没有提供调用")
        if reply.stop_reason == "stop" and calls:
            raise ValueError("模型声明回答结束，却仍包含工具调用")
        history.append(reply.message)
        if not calls:
            return history
        for call in calls:
            result = runner.run(call)
            history.append(Message("tool", [result]))
    raise RuntimeError("练习已达到 4 轮模型请求上限")
