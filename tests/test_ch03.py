from pathlib import Path
from minicode.agent import Agent
from minicode.providers import ModelConfig, create_model
from minicode.providers.demo import run_demo
from minicode.testing import FakeModel, call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, ModelReply, ToolCall, ToolSpec, Usage


def test_chat_http_tool_roundtrip():
    events, requests = run_demo("openai-chat")
    assert events[-1]["text"] == "结果是 5。"
    assert requests[1]["messages"][-1] == {"role": "tool", "tool_call_id": "call_1", "content": "5"}


def test_migrated_runner_accepts_valid_args_and_reports_parse_error(tmp_path):
    touched = []
    runner = ToolRunner([Tool(ToolSpec("echo", "echo", {"type": "object"}),
                             lambda args, ctx: touched.append(args) or "ok")], ToolContext(tmp_path))
    assert runner.run(call("echo", value=2)).content == "ok"
    assert runner.run(ToolCall("bad", "echo", None, "{", "incomplete JSON")).is_error
    assert touched == [{"value": 2}]


def test_truncated_reply_does_not_execute(tmp_path):
    touched = []
    runner = ToolRunner([Tool(ToolSpec("echo", "echo", {"type": "object"}),
                             lambda args, ctx: touched.append(args))], ToolContext(tmp_path))
    reply = ModelReply(Message("assistant", [call("echo")]), Usage(7, 1), "length")
    final = list(Agent(FakeModel([reply]), runner).run("test"))[-1]
    assert final["status"] == "length" and final["usage"]["output_tokens"] == 1
    assert not touched


def test_deepseek_limit_is_encoded_with_supported_field():
    config = ModelConfig("openai-chat", "fixture", "https://fixture.invalid", "fake", provider="deepseek", max_tokens=1)
    model = create_model(config)
    try:
        body = model.encode([Message.text("user", "hi")], [])
        assert body["max_tokens"] == 1 and "max_completion_tokens" not in body
    finally:
        model.close()
