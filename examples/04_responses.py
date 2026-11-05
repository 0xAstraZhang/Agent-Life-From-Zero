"""第 04 章（选修）：默认调用真实 Responses；--offline 运行确定的回归。"""
from lessons.protocol_roundtrip import addition_runner
from lessons.wire_common import run_wire_demo
from lessons.responses_prototype import responses_roundtrip
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.testing import call


def main(argv=None):
    args = parse_args(__doc__, argv, default_api="openai-responses")
    if args.api != "openai-responses":
        raise ValueError("第 04 章演示 openai-responses；其他协议请使用对应章节")
    if args.step == "minimal":
        run_wire_demo(args, responses_roundtrip)
        return
    if args.offline:
        from minicode.providers.demo import run_demo
        run_demo(args.api)
        return
    with open_model(args, [call("add", a=2, b=3), "结果是 5。"]) as model:
        runner = addition_runner()
        events = run_events(Agent(model, runner).run("请调用 add 工具计算 2 加 3，再回答。"))
        calls = [request for event in events for request in event.get("tool_calls", ())]
        if len(calls) != 1 or calls[0].name != "add" or calls[0].arguments != {"a": 2, "b": 3}:
            raise RuntimeError("模型没有按要求调用 add(2, 3)")
        results = [event for event in events if event["type"] == "tool_result"]
        if len(results) != 1 or results[0]["is_error"] or results[0]["content"] != "5":
            raise RuntimeError("模型没有实际完成一次 add(2, 3)")


if __name__ == "__main__":
    main()
