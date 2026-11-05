"""第 8 章：先看真实文本增量，再接回工具循环。"""
from lessons.protocol_roundtrip import addition_runner
from lessons.runtime import open_model, parse_args
from lessons.streaming_prototype import request_stream, stream_roundtrip
from minicode.agent import Agent
from minicode.testing import call
from minicode.types import Message, ToolResult, tool_calls


def main(argv=None):
    args = parse_args(__doc__, argv, steps=("text", "tools", "enhanced", "transport"), default_step="text")
    if args.step == "transport":
        if not args.offline:
            raise ValueError("transport 是协议离线回归，请同时传入 --offline")
        from minicode.providers.demo import run_demo
        for api in ("openai-chat", "openai-responses", "anthropic-messages"):
            run_demo(api, stream=True)
        return
    script = ["模型的回答正在逐段到达。"] if args.step == "text" else [call("add", a=2, b=3), "结果是 5。"]
    with open_model(args, script) as model:
        if args.step == "text":
            fragments = []

            def show(text):
                fragments.append(text)
                print(text, end="", flush=True)

            reply = request_stream(model, [Message.text("user", "用两句话解释 Agent 如何使用工具。")], on_text=show)
            if reply.stop_reason != "stop":
                raise RuntimeError(f"文本回复未完成：{reply.stop_reason}")
            if not "".join(fragments).strip():
                raise RuntimeError("没有收到可显示的文本增量")
            print("\n本轮状态：", reply.stop_reason)
        elif args.step == "tools":
            history = stream_roundtrip(model, addition_runner(), "请调用 add 计算 2 加 3，再回答。")
            calls = [request for message in history for request in tool_calls(message)]
            if len(calls) != 1 or calls[0].name != "add" or calls[0].arguments != {"a": 2, "b": 3}:
                raise RuntimeError("模型没有按要求调用 add(2, 3)")
            results = [part for message in history for part in message.content if isinstance(part, ToolResult)]
            if len(results) != 1 or results[0].is_error or results[0].content != "5":
                raise RuntimeError("没有完成加法工具往返")
            print()
        else:
            events = []
            for event in Agent(model, addition_runner(), stream=True).run("请调用 add 计算 2 加 3，再回答。"):
                events.append(event)
                if event["type"] == "text_delta":
                    print(event["text"], end="", flush=True)
                elif event["type"] == "result":
                    print("\n任务状态：", event["status"])
            if not events or events[-1]["type"] != "result" or events[-1]["status"] != "success":
                raise RuntimeError("流式任务未正常完成")
            calls = [request for event in events for request in event.get("tool_calls", ())]
            if len(calls) != 1 or calls[0].name != "add" or calls[0].arguments != {"a": 2, "b": 3}:
                raise RuntimeError("模型没有按要求调用 add(2, 3)")
            results = [event for event in events if event["type"] == "tool_result"]
            if len(results) != 1 or results[0]["is_error"] or results[0]["content"] != "5":
                raise RuntimeError("没有完成加法工具往返")


if __name__ == "__main__":
    main()
