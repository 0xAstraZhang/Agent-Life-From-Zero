"""第 18 章离线实验：显式思考参数、一次 HTTP 重试、按行读取和请求计量。"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.permissions import PermissionRunner
from minicode.providers import ModelConfig, create_model
from minicode.tool import ToolContext, ToolRunner


def main():
    requests, trace = [], []

    def serve(request):
        body = json.loads(request.content)
        requests.append(body)
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "high"
        if len(requests) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "try again"})
        message = {"role": "assistant", "content": "已读取第二行：answer = 42", "reasoning_content": ""}
        reason = "stop"
        if len(requests) == 2:
            message["content"] = None
            message["tool_calls"] = [{
                "id": "read-1", "type": "function", "function": {
                    "name": "Read", "arguments": json.dumps({"path": "answer.py", "offset": 2, "limit": 1})
                },
            }]
            reason = "tool_calls"
        return httpx.Response(200, headers={"x-request-id": f"fixture-{len(requests)}"}, json={
            "model": "deepseek-flash",
            "choices": [{"message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3,
                      "prompt_cache_hit_tokens": 2, "prompt_cache_miss_tokens": 8},
        })

    config = ModelConfig("openai-chat", "deepseek-flash", "https://fixture.invalid", "fixture-key",
                         provider="deepseek", thinking="enabled", reasoning_effort="high",
                         max_retries=1, retry_base_delay=0, retry_max_delay=0)
    with TemporaryDirectory(prefix="minicode-runtime-") as directory, httpx.Client(transport=httpx.MockTransport(serve)) as client:
        cwd = Path(directory)
        (cwd / "answer.py").write_text("# example\nanswer = 42\n", encoding="utf-8")
        model = create_model(config, client)
        model.observer = trace.append
        runner = PermissionRunner(ToolRunner(build_coding_tools(include_bash=False), ToolContext(cwd)), read_only=True)
        events = list(Agent(model, runner, task_timeout=10, observer=trace.append).run("读取 answer.py 的第二行"))
        final = events[-1]
        assert final["status"] == "success"
        assert final["usage"] == {"input_tokens": 20, "output_tokens": 6}
        tool_result = next(event for event in events if event["type"] == "tool_result")
        assert "answer = 42" in tool_result["content"] and "# example" not in tool_result["content"]
        print("思考参数：enabled / high")
        print("HTTP 尝试次数：", len(requests))
        print("请求重试次数：", sum(event["type"] == "request_retry" for event in trace))
        print("分段读取：", tool_result["content"].strip())
        print("终止状态：", final["status"])
        print("观测到的 token：", final["usage"])


if __name__ == "__main__":
    main()
