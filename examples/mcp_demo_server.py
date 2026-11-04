"""只用于第 14 章：MCP 2025-11-25 stdio echo 服务器，无外部依赖。"""
import json
import sys

initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if "id" not in request:
        if method == "notifications/initialized":
            initialized = True
        continue
    reply = {"jsonrpc": "2.0", "id": request["id"]}
    if method == "initialize":
        reply["result"] = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                           "serverInfo": {"name": "demo", "version": "1.0"}}
    elif not initialized:
        reply["error"] = {"code": -32000, "message": "not initialized"}
    elif method == "tools/list":
        reply["result"] = {"tools": [{"name": "echo", "description": "返回给定文本；error 前缀模拟失败。",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}},
                            "required": ["text"], "additionalProperties": False},
            "annotations": {"readOnlyHint": True}}]}
    elif method == "tools/call" and request["params"]["name"] == "echo":
        text = request["params"]["arguments"]["text"]
        reply["result"] = {"content": [{"type": "text", "text": text}], "isError": text.startswith("error:")}
    else:
        reply["error"] = {"code": -32601, "message": "unknown method or tool"}
    print(json.dumps(reply, ensure_ascii=False), flush=True)
