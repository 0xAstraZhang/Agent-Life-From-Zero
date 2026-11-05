"""第 01 章：真实模型 → 本地加法 → 回传结果 → 再请求模型。"""
import json

from .ch00 import lesson_args, open_chat, text_reply


ADD = {"type": "function", "function": {
    "name": "add", "description": "计算两个整数的和",
    "parameters": {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False},
}}


def add(request):
    if request["function"]["name"] != "add":
        raise ValueError("本章只提供 add 工具")
    arguments = json.loads(request["function"]["arguments"])
    return str(arguments["a"] + arguments["b"])


def run(model, prompt, *, enhanced=False, max_turns=4):
    messages = [{"role": "user", "content": prompt}]
    for turn in range(max_turns):
        reply = model.complete(messages, [ADD])
        message = reply["message"]
        calls = message.get("tool_calls") or []
        if enhanced:
            check_reply(reply, messages)
        if reply["finish_reason"] == "stop" and not calls:
            messages.append(message)
            return message.get("content") or "", messages
        if reply["finish_reason"] != "tool_calls":
            raise RuntimeError("模型回复尚未正常完成：" + str(reply["finish_reason"]))
        messages.append(message)
        for request in calls:
            result = add_checked(request) if enhanced else add(request)
            messages.append({"role": "tool", "tool_call_id": request["id"], "content": result})
    raise RuntimeError("已达到模型请求轮数上限")


def check_reply(reply, messages):
    """增强步骤：把每一种不完整回复单独说清楚。"""
    message = reply["message"]
    calls = message.get("tool_calls") or []
    reason = reply["finish_reason"]
    if reason == "tool_calls" and not calls:
        raise ValueError("模型要求调用工具，但没有提供工具调用")
    if reason == "stop" and calls:
        raise ValueError("模型声明回答结束，但仍包含工具调用")
    prior_ids = {call["id"] for item in messages for call in item.get("tool_calls", [])}
    ids = [call.get("id") for call in calls]
    if any(not call_id for call_id in ids):
        raise ValueError("工具调用缺少 ID")
    if len(set(ids)) != len(ids) or prior_ids.intersection(ids):
        raise ValueError("工具调用 ID 重复，无法配对结果")


def add_checked(request):
    try:
        if request["function"]["name"] != "add":
            raise ValueError("未知工具：" + request["function"]["name"])
        arguments = json.loads(request["function"]["arguments"])
        if not isinstance(arguments, dict) or set(arguments) != {"a", "b"}:
            raise ValueError("add 需要 a 和 b 两个参数")
        if type(arguments["a"]) is not int or type(arguments["b"]) is not int:
            raise ValueError("a 和 b 必须是整数")
        return str(arguments["a"] + arguments["b"])
    except (ValueError, TypeError, KeyError) as error:
        return f"工具错误：{error}"


def tool_reply(name, arguments, call_id="call_1"):
    """离线检查夹具；测试也用它构造可预测的 API 响应。"""
    return {"message": {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(arguments, ensure_ascii=False)}}]},
        "finish_reason": "tool_calls"}


def main(argv=None):
    args = lesson_args(__doc__, argv)
    replies = [tool_reply("add", {"a": 2, "b": 3}), text_reply("2 + 3 = 5。")]
    if args.step == "enhanced":
        replies = [tool_reply("add", {"a": "2", "b": 3}, "bad_1"), *replies]
    with open_chat(offline=args.offline, replies=replies) as model:
        answer, messages = run(model, "请调用 add 工具计算 2 加 3，再告诉我结果。",
                               enhanced=args.step == "enhanced")
    if not any(message["role"] == "tool" and message["content"] == "5" for message in messages):
        raise RuntimeError("本章尚未完成：需要实际调用 add 并把结果 5 回传模型")
    for message in messages:
        if message["role"] == "tool":
            print("工具结果：", message["content"])
    print("最终回答：", answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
