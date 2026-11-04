"""本阶段的离线 HTTP 工具往返；没有流式或未来协议依赖。"""
import json
from pathlib import Path
import httpx
from ..agent import Agent
from ..tool import Tool, ToolContext, ToolRunner
from ..types import ToolSpec
from . import ModelConfig, create_model


def payload(api: str, first: bool) -> dict:
    if api == "openai-chat":
        message = {"role": "assistant", "content": "" if first else "结果是 5。"}
        if first:
            message["tool_calls"] = [{"id": "call_1", "type": "function", "function": {
                "name": "add", "arguments": '{"a":2,"b":3}'}}]
        return {"choices": [{"message": message, "finish_reason": "tool_calls" if first else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    if api == "openai-responses":
        output = ([{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "add",
                    "arguments": '{"a":2,"b":3}'}] if first else
                  [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "结果是 5。"}]}])
        return {"status": "completed", "output": output, "usage": {"input_tokens": 10, "output_tokens": 5}}
    return {"type": "message", "role": "assistant", "content":
            ([{"type": "tool_use", "id": "call_1", "name": "add", "input": {"a": 2, "b": 3}}] if first else
             [{"type": "text", "text": "结果是 5。"}]),
            "stop_reason": "tool_use" if first else "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}}


def run_demo(api):
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=payload(api, len(requests) == 1))
    spec = ToolSpec("add", "把两个整数相加", {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"], "additionalProperties": False})
    runner = ToolRunner([Tool(spec, lambda args, ctx: str(args["a"] + args["b"]), read_only=True)],
                        ToolContext(Path.cwd()))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = create_model(ModelConfig(api, "fixture-model", "https://fixture.invalid/v1", "fixture-key"), client)
        events = list(Agent(model, runner).run("2 加 3 等于多少？"))
    assert len(requests) == 2 and events[-1]["status"] == "success"
    print(f"{api}: 两次 HTTP 请求，一次本地工具调用")
    print(events[-1]["text"])
    return events, requests
