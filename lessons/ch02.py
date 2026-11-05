"""第 02 章：用注册表增加第二个工具，再加入参数校验。"""
import json

from jsonschema import Draft202012Validator, ValidationError

from .ch00 import lesson_args, open_chat, text_reply
from .ch01 import ADD, check_reply, tool_reply


MULTIPLY = {"type": "function", "function": {
    "name": "multiply", "description": "计算两个整数的积",
    "parameters": ADD["function"]["parameters"],
}}
TOOLS = {
    "add": {"spec": ADD, "execute": lambda a, b: a + b},
    "multiply": {"spec": MULTIPLY, "execute": lambda a, b: a * b},
}


def execute(request, tools):
    function = request["function"]
    tool = tools[function["name"]]
    arguments = json.loads(function["arguments"])
    return str(tool["execute"](**arguments))


def run(model, prompt, tools=TOOLS, *, enhanced=False, max_turns=6):
    messages = [{"role": "user", "content": prompt}]
    specs = [tool["spec"] for tool in tools.values()]
    for turn in range(max_turns):
        reply = model.complete(messages, specs)
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
            result = execute_checked(request, tools) if enhanced else execute(request, tools)
            messages.append({"role": "tool", "tool_call_id": request["id"], "content": result})
    raise RuntimeError("已达到模型请求轮数上限")


def execute_checked(request, tools):
    function = request["function"]
    if function["name"] not in tools:
        return "工具错误：未知工具 " + function["name"]
    tool = tools[function["name"]]
    try:
        arguments = json.loads(function["arguments"])
        Draft202012Validator(tool["spec"]["function"]["parameters"]).validate(arguments)
        return str(tool["execute"](**arguments))
    except (ValueError, TypeError, ValidationError) as error:
        return f"工具错误：{error.message if isinstance(error, ValidationError) else error}"


def main(argv=None):
    args = lesson_args(__doc__, argv)
    replies = [tool_reply("add", {"a": 2, "b": 3}, "sum"),
               tool_reply("multiply", {"a": 5, "b": 4}, "product"), text_reply("结果是 20。")]
    if args.step == "enhanced":
        replies.insert(0, tool_reply("add", {"a": "2", "b": 3}, "bad_sum"))
    with open_chat(offline=args.offline, replies=replies) as model:
        answer, messages = run(model, "请先调用 add 计算 2 加 3，再调用 multiply 把结果乘以 4。",
                               enhanced=args.step == "enhanced")
    results = {message["tool_call_id"]: message["content"] for message in messages if message["role"] == "tool"}
    completed = {(request["function"]["name"], results.get(request["id"]))
                 for message in messages for request in message.get("tool_calls", [])}
    if not {("add", "5"), ("multiply", "20")} <= completed:
        raise RuntimeError("本章尚未完成：需要实际调用 add 得到 5，再调用 multiply 得到 20")
    for message in messages:
        if message["role"] == "tool":
            print("工具结果：", message["content"])
    print("最终回答：", answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
