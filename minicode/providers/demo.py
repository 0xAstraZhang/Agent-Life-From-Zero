"""Offline HTTP demonstrations used by chapters 03–05 and 08."""
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


def frames(api, first):
    result = payload(api, first)
    if api == "openai-chat":
        message = result["choices"][0]["message"]
        if first:
            call = message["tool_calls"][0]
            yield {"choices": [{"index": 0, "delta": {"tool_calls": [dict(call, index=0)]}, "finish_reason": None}]}
        else:
            for text in ("结果是 ", "5。"):
                yield {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
        yield {"choices": [{"index": 0, "delta": {}, "finish_reason": result["choices"][0]["finish_reason"]}],
               "usage": result["usage"]}
        yield "[DONE]"
    elif api == "openai-responses":
        if first:
            item = result["output"][0]
            yield {"type": "response.output_item.added", "item": dict(item, arguments=""), "output_index": 0}
            yield {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": item["arguments"]}
        else:
            for text in ("结果是 ", "5。"):
                yield {"type": "response.output_text.delta", "delta": text}
        yield {"type": "response.completed", "response": result}
    else:
        yield {"type": "message_start", "message": {"type": "message", "role": "assistant", "content": [],
                                                       "usage": {"input_tokens": 10, "output_tokens": 0}}}
        block = result["content"][0]
        yield {"type": "content_block_start", "index": 0, "content_block":
               dict(block, input={}) if first else {"type": "text", "text": ""}}
        if first:
            yield {"type": "content_block_delta", "index": 0, "delta": {
                "type": "input_json_delta", "partial_json": '{"a":2,"b":3}'}}
        else:
            for text in ("结果是 ", "5。"):
                yield {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}
        yield {"type": "content_block_stop", "index": 0}
        yield {"type": "message_delta", "delta": {"stop_reason": result["stop_reason"]}, "usage": {"output_tokens": 5}}
        yield {"type": "message_stop"}


def run_demo(api: str, stream: bool = False):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        first = len(requests) == 1
        if stream:
            data = "".join("data: " + (event if isinstance(event, str) else json.dumps(event)) + "\n\n"
                           for event in frames(api, first))
            return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=payload(api, first))

    spec = ToolSpec("add", "把两个整数相加", {"type": "object", "properties": {
        "a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"], "additionalProperties": False})
    runner = ToolRunner([Tool(spec, lambda args, ctx: str(args["a"] + args["b"]), read_only=True)], ToolContext(Path.cwd()))
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = create_model(ModelConfig(api, "fixture-model", "https://fixture.invalid/v1", "fixture-key"), client)
        events = list(Agent(model, runner, stream=stream).run("2 加 3 等于多少？"))
    assert len(requests) == 2 and events[-1]["status"] == "success"
    print(f"{api}: 两次 HTTP 请求，一次本地工具调用")
    if stream:
        print("文本增量：", [e["text"] for e in events if e["type"] == "text_delta"])
    print(events[-1]["text"])
    return events, requests
