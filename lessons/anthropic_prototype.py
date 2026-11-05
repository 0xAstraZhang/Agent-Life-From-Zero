"""第 05 章：直接发送两次 Messages 请求，执行一次加法。"""
from lessons.wire_common import PARAMETERS, add, post


def anthropic_roundtrip(client, config, a=2, b=3):
    headers = {"x-api-key": config.api_key, "anthropic-version": "2023-06-01"}
    body = {"model": config.model, "max_tokens": config.max_tokens,
            "messages": [{"role": "user", "content": f"请调用 add 计算 {a} 加 {b}，再回答。"}],
            "tools": [{"name": "add", "description": "两个整数相加", "input_schema": PARAMETERS}]}
    first = post(client, config, "/messages", headers, body)
    if first.get("stop_reason") != "tool_use":
        raise ValueError("本次没有完整的工具调用，请确认模型支持工具")
    calls = [block for block in first["content"] if block["type"] == "tool_use"]
    if len(calls) != 1 or calls[0]["name"] != "add" or calls[0]["input"] != {"a": a, "b": b}:
        raise ValueError(f"本次练习需要实际调用一次 add({a}, {b})")
    body["messages"].append({"role": "assistant", "content": first["content"]})
    results = []
    for block in first["content"]:
        if block["type"] == "tool_use":
            result = add(block["name"], block["input"])
            print("本地 add →", result)
            results.append({"type": "tool_result", "tool_use_id": block["id"], "content": result})
    if not results:
        raise ValueError("回复缺少工具调用内容")
    body["messages"].append({"role": "user", "content": results})
    final = post(client, config, "/messages", headers, body)
    if final.get("stop_reason") not in {"end_turn", "stop_sequence"}:
        raise ValueError("模型尚未给出最终回答；多轮任务使用增强版本")
    text = "".join(block["text"] for block in final["content"] if block["type"] == "text")
    if not text.strip():
        raise ValueError("没有收到模型的最终文本回答")
    print(text)
    return text
