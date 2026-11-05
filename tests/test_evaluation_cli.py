"""通过实际适配器和离线 HTTP fixture 验证 CLI 的完整参数与计量链。"""
import json

import httpx
import pytest

from minicode.cli import main, model_base_url, parser
from minicode.providers import create_model


@pytest.mark.parametrize("api", ["openai-chat", "openai-responses", "anthropic-messages"])
def test_cli_controls_and_usage_survive_the_complete_request_path(api, tmp_path, monkeypatch, capsys):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        if api == "openai-chat":
            response = {"model": "deepseek-flash", "choices": [{"message": {"role": "assistant", "content": "done"},
                          "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 100, "prompt_cache_hit_tokens": 40, "completion_tokens": 10}}
        elif api == "openai-responses":
            response = {"model": "deepseek-flash", "status": "completed", "output": [{"type": "message", "role": "assistant",
                        "content": [{"type": "output_text", "text": "done"}]}],
                        "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 40}, "output_tokens": 10}}
        else:
            response = {"model": "deepseek-flash", "content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn",
                        "usage": {"input_tokens": 60, "cache_read_input_tokens": 40, "output_tokens": 10}}
        return httpx.Response(200, json=response, headers={"x-request-id": "fixture-request"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    monkeypatch.setattr("minicode.cli.create_model", lambda config: create_model(config, client))
    monkeypatch.setenv("MINICODE_API_KEY", "super-secret-key")
    price_path = tmp_path / "prices.json"
    price_path.write_text(json.dumps({"model": "deepseek-flash", "currency": "USD", "as_of": "2026-11-05",
        "input_per_million": 2, "output_per_million": 8, "cache_read_per_million": .2, "cache_write_per_million": 3}))
    log = tmp_path / "events.jsonl"
    try:
        code = main(["--cwd", str(tmp_path), "--provider", "deepseek", "--model", "deepseek-flash",
                     "--api", api, "--base-url", "https://fixture.invalid/v1", "--thinking", "enabled",
                     "--reasoning-effort", "high", "--request-timeout", "17", "--max-retries", "0",
                     "--task-timeout", "5", "--parallel-tools", "2", "--event-log", str(log),
                     "--prices", str(price_path), "--json", "-p", "inspect"])
    finally:
        client.close()
    assert code == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["type"] == "result" and events[-1]["status"] == "success"
    metrics = events[-1]["metrics"]
    assert metrics["observed_usage"] == {"input_tokens": 100, "output_tokens": 10}
    assert metrics["estimated_cost"] == pytest.approx((60 * 2 + 40 * .2 + 10 * 8) / 1_000_000)
    if api == "openai-responses":
        assert requests[0]["reasoning"] == {"effort": "high"}
    else:
        assert requests[0]["thinking"] == {"type": "enabled"}
        assert (requests[0]["reasoning_effort"] if api == "openai-chat" else requests[0]["output_config"]["effort"]) == "high"
    text = log.read_text()
    assert "super-secret-key" not in text
    recorded = [json.loads(line) for line in text.splitlines()]
    ends = [event for event in recorded if event["type"] == "request_end"]
    assert len(ends) == 1 and ends[0]["request_id"] == "fixture-request"
    assert recorded[0]["model_config"]["timeout"] == 17


def test_json_object_mode_adds_instruction_and_checks_final_response(tmp_path, monkeypatch, capsys):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "not json"},
            "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr("minicode.cli.create_model", lambda config: create_model(config, client))
        monkeypatch.setenv("MINICODE_API_KEY", "fixture-key")
        assert main(["--cwd", str(tmp_path), "--provider", "deepseek", "--model", "deepseek-flash",
                     "--response-format", "json_object", "--json", "-p", "answer"]) == 1
    assert "JSON" in requests[0]["messages"][0]["content"]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["status"] == "error"
    assert events[-1]["metrics"]["observed_usage"]["output_tokens"] == 2


def test_event_log_cannot_replace_session_file(tmp_path, capsys):
    target = tmp_path / "history.jsonl"
    assert main(["--demo", "--session", str(target), "--event-log", str(target)]) == 2
    assert not target.exists()


def test_strict_builtin_tools_execute_optional_null_arguments(tmp_path, monkeypatch, capsys):
    (tmp_path / "value.txt").write_text("verified content")
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            read = next(tool["function"] for tool in body["tools"] if tool["function"]["name"] == "Read")
            assert read["strict"] and set(read["parameters"]["required"]) == {"path", "offset", "limit", "column"}
            message = {"role": "assistant", "content": None, "tool_calls": [{"id": "read1", "type": "function",
                       "function": {"name": "Read", "arguments": json.dumps({"path": "value.txt", "offset": None,
                                      "limit": None, "column": None})}}]}
            reason = "tool_calls"
        else:
            assert "verified content" in body["messages"][-1]["content"]
            message, reason = {"role": "assistant", "content": "done"}, "stop"
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": reason}],
                                       "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr("minicode.cli.create_model", lambda config: create_model(config, client))
        monkeypatch.setenv("MINICODE_API_KEY", "fixture-key")
        assert main(["--cwd", str(tmp_path), "--provider", "openai", "--model", "gpt-4.1", "--read-only",
                     "--strict-tools", "--json", "-p", "read value.txt"]) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["status"] == "success"
    assert len(requests) == 2


@pytest.mark.parametrize("api,expected", [
    ("openai-chat", "https://api.deepseek.com"),
    ("openai-responses", "https://api.deepseek.com"),
    ("anthropic-messages", "https://api.deepseek.com/anthropic/v1"),
])
def test_deepseek_default_and_known_explicit_routes(api, expected, monkeypatch):
    monkeypatch.delenv("MINICODE_BASE_URL", raising=False)
    args = parser().parse_args(["--provider", "deepseek", "--api", api])
    assert model_base_url(args) == expected
    args.base_url = "https://api.deepseek.com/anthropic"
    assert model_base_url(args) == expected
    args.base_url = "https://proxy.invalid/custom"
    assert model_base_url(args) == args.base_url


def test_forced_tool_choice_and_final_schema_do_not_trap_loop_or_reach_subagent(tmp_path, monkeypatch, capsys):
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps({"type": "object", "properties": {"answer": {"type": "string"}},
                                       "required": ["answer"], "additionalProperties": False}))
    parent, child = [], []

    def reply(message, reason):
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": reason}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

    def handle(request):
        body = json.loads(request.content)
        if "只读调查" in body["messages"][0]["content"]:
            child.append(body)
            return reply({"role": "assistant", "content": "结论：在 notes.md。"}, "stop")
        parent.append(body)
        if body.get("tool_choice"):
            return reply({"role": "assistant", "content": None, "tool_calls": [{"id": "d1", "type": "function",
                          "function": {"name": "Delegate", "arguments": json.dumps({"task": "找笔记"})}}]}, "tool_calls")
        return reply({"role": "assistant", "content": json.dumps({"answer": "notes.md"})}, "stop")

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr("minicode.cli.create_model", lambda config: create_model(config, client))
        monkeypatch.setenv("MINICODE_API_KEY", "fixture-key")
        code = main(["--cwd", str(tmp_path), "--provider", "openai", "--model", "gpt-4.1", "--subagents",
                     "--tool-choice", "tool:Delegate", "--response-format", "json_schema",
                     "--response-schema", str(schema_path), "--json", "-p", "where are the notes?"])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == 0 and events[-1]["status"] == "success"
    # 强制选择只作用于第一次请求；工具结果回来后交回服务商默认，任务才能结束。
    assert parent[0]["tool_choice"] == {"type": "function", "function": {"name": "Delegate"}}
    assert len(parent) == 2 and "tool_choice" not in parent[1]
    assert all(body["response_format"]["type"] == "json_schema" for body in parent)
    # 子 Agent 没有 Delegate 工具，且只返回散文摘要：两项父任务控制都不能继承。
    assert len(child) == 1 and "tool_choice" not in child[0] and "response_format" not in child[0]
    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert tool_results and not tool_results[0]["is_error"]
