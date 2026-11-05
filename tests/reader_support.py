"""正文项目验证的 HTTP 响应夹具；不会复制进读者项目。"""
import json


def chat(text, *, finish_reason="stop"):
    return {"id": "reader-response", "object": "chat.completion", "model": "reader-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}


def tool(name, args, call_id="c1"):
    response = chat(None, finish_reason="tool_calls")
    response["choices"][0]["message"]["tool_calls"] = [{
        "id": call_id, "type": "function", "function": {
            "name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }]
    return response
