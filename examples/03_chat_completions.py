"""第 03 章：用正式适配器完成同一工具往返；默认请求真实 API。"""
from pathlib import Path

from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, ToolSpec, message_text, tool_calls


def minimal_roundtrip(model, runner):
    messages = [Message.text("user", "请调用 add 工具计算 2 加 3，再告诉我结果。")]
    first = model.complete(messages, runner.specs())
    if first.stop_reason != "tool_calls" or not tool_calls(first.message):
        raise RuntimeError("本次练习需要模型返回一个完整的 add 调用")
    messages.append(first.message)
    for request in tool_calls(first.message):
        result = runner.run(request)
        if result.is_error:
            raise RuntimeError("工具调用没有完成：" + result.content)
        messages.append(Message("tool", [result]))
        print("工具结果：", result.content)
    second = model.complete(messages, runner.specs())
    if second.stop_reason != "stop" or tool_calls(second.message):
        raise RuntimeError("模型尚未给出最终回答；增强步骤会继续循环")
    return message_text(second.message)


def main(argv=None):
    args = parse_args(__doc__, argv, default_api="openai-chat")
    spec = ToolSpec("add", "计算两个整数的和", {
        "type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False})
    executed = []

    def add(args, context):
        result = str(args["a"] + args["b"])
        executed.append(result)
        return result

    runner = ToolRunner([Tool(spec, add, read_only=True)],
                        ToolContext(Path.cwd()))
    with open_model(args, [call("add", a=2, b=3), "结果是 5。"]) as model:
        if args.step == "minimal":
            print("最终回答：", minimal_roundtrip(model, runner))
        else:
            run_events(Agent(model, runner, max_turns=4).run("请调用 add 计算 2 加 3，再回答。"))
    if "5" not in executed:
        raise RuntimeError("本章尚未完成：需要实际调用 add 得到 5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
