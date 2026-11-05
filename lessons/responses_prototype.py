"""第 04 章：直接发送两次 Responses 请求，执行一次加法。"""
import json

from lessons.wire_common import PARAMETERS, add, post


def responses_roundtrip(client, config, a=2, b=3):
    headers = {"Authorization": f"Bearer {config.api_key}"}
    body = {"model": config.model, "max_output_tokens": config.max_tokens, "store": False,
            "include": ["reasoning.encrypted_content"],
            "input": [{"role": "user", "content": f"请调用 add 计算 {a} 加 {b}，再回答。"}],
            "tools": [{"type": "function", "name": "add", "description": "两个整数相加",
                       "parameters": PARAMETERS, "strict": False}]}
    first = post(client, config, "/responses", headers, body)
    if first.get("status") != "completed":
        raise ValueError("模型响应未完成，停止本次练习")
    body["input"].extend(first["output"])
    calls = [item for item in first["output"] if item["type"] == "function_call"]
    if not calls:
        raise ValueError("模型没有调用 add；请确认模型支持工具")
    if len(calls) != 1 or calls[0]["name"] != "add" or json.loads(calls[0]["arguments"]) != {"a": a, "b": b}:
        raise ValueError(f"本次练习需要实际调用一次 add({a}, {b})")
    for call in calls:
        result = add(call["name"], json.loads(call["arguments"]))
        print("本地 add →", result)
        body["input"].append({"type": "function_call_output", "call_id": call["call_id"], "output": result})
    final = post(client, config, "/responses", headers, body)
    if final.get("status") != "completed" or any(item["type"] == "function_call" for item in final["output"]):
        raise ValueError("模型尚未给出最终回答；多轮任务使用增强版本")
    text = "".join(block["text"] for item in final["output"] if item["type"] == "message"
                   for block in item["content"] if block["type"] == "output_text")
    if not text.strip():
        raise ValueError("没有收到模型的最终文本回答")
    print(text)
    return text
