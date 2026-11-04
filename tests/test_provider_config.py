"""参数兼容和环境来源的边界；不访问真实模型。"""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys

import httpx
import pytest

from minicode.providers import ModelConfig, create_model
from minicode.types import Message

ROOT = Path(__file__).resolve().parents[1]


def live_module():
    spec = importlib.util.spec_from_file_location("live_example", ROOT / "examples" / "live_model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("provider,override,expected", [
    ("openai", None, "max_completion_tokens"),
    ("custom", None, "max_completion_tokens"),
    ("deepseek", None, "max_tokens"),
    ("deepseek", "max_completion_tokens", "max_completion_tokens"),
    ("custom", "max_tokens", "max_tokens"),
])
@pytest.mark.parametrize("stream", [False, True])
def test_chat_output_limit_reaches_http_in_both_modes(provider, override, expected, stream):
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        if stream:
            event = {"choices": [{"index": 0, "delta": {"content": ""}, "finish_reason": "length"}],
                     "usage": {"prompt_tokens": 5, "completion_tokens": 1}}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": ""},
                                                     "finish_reason": "length"}],
                                       "usage": {"prompt_tokens": 5, "completion_tokens": 1}})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        model = create_model(ModelConfig("openai-chat", "test", "https://fixture.invalid/v1", "test-key",
                                         provider=provider, max_tokens=1, chat_token_field=override), client)
        if stream:
            reply = list(model.stream([Message.text("user", "hello")]))[-1].reply
        else:
            reply = model.complete([Message.text("user", "hello")])
    request = requests[0]
    assert request[expected] == 1
    assert (set(request) & {"max_tokens", "max_completion_tokens"}) == {expected}
    assert reply.stop_reason == "length" and reply.usage.output_tokens == 1


def test_invalid_chat_field_is_rejected_before_http():
    with pytest.raises(ValueError, match="chat_token_field"):
        ModelConfig("openai-chat", "test", "https://fixture.invalid", "key", chat_token_field="max_tokenss")


def test_factory_does_not_import_future_protocols(tmp_path):
    for name in ("__init__.py", "types.py", "providers/__init__.py", "providers/base.py", "providers/chat.py"):
        destination = tmp_path / "minicode" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "minicode" / name, destination)
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from minicode.providers import ModelConfig, create_model
model = create_model(ModelConfig('openai-chat', 'test', 'https://fixture.invalid', 'key'))
model.close()
assert 'minicode.providers.responses' not in sys.modules
assert 'minicode.providers.anthropic' not in sys.modules
"""
    subprocess.run([sys.executable, "-I", "-c", code, str(tmp_path)], cwd=tmp_path, check=True)


def test_live_env_never_combines_keys_from_different_groups():
    live = live_module()
    env = {"LLM_MODEL": "legacy", "LLM_BASE_URL": "https://api.deepseek.com", "LLM_API_KEY": "legacy-key",
           "MINICODE_MODEL": "new-model"}
    with pytest.raises(ValueError, match="MINICODE_API_KEY"):
        live.config_from_env(live.parser().parse_args([]), env)
    config = live.config_from_env(live.parser().parse_args(["--env-prefix", "LLM"]), env)
    assert config.model == "legacy" and config.api_key == "legacy-key"
    assert config.provider == "deepseek" and config.chat_token_parameter == "max_tokens"
    env.update(MINICODE_BASE_URL="https://another.invalid/v1", MINICODE_API_KEY="new-key")
    config = live.config_from_env(live.parser().parse_args([]), env)
    assert config.model == "new-model" and config.api_key == "new-key"
    assert config.base_url == "https://another.invalid/v1"


@pytest.mark.parametrize("api,expected", [
    ("openai-chat", "https://api.deepseek.com"),
    ("openai-responses", "https://api.deepseek.com"),
    ("anthropic-messages", "https://api.deepseek.com/anthropic/v1"),
])
def test_live_known_deepseek_routes(api, expected):
    live = live_module()
    env = {"LLM_MODEL": "deepseek-flash", "LLM_BASE_URL": "https://api.deepseek.com", "LLM_API_KEY": "key"}
    config = live.config_from_env(live.parser().parse_args(["--api", api]), env)
    assert config.base_url == expected
    assert config.model == "deepseek-flash"


def test_live_unknown_service_keeps_explicit_url():
    live = live_module()
    env = {"LLM_MODEL": "test", "LLM_BASE_URL": "https://api.deepseek.com.evil.invalid/custom", "LLM_API_KEY": "key"}
    config = live.config_from_env(live.parser().parse_args(["--api", "anthropic-messages"]), env)
    assert config.base_url == env["LLM_BASE_URL"] and config.provider == "custom"


def test_live_nonstream_does_not_require_future_agent_keyword(monkeypatch):
    from minicode.agent import Agent
    from minicode.providers.demo import payload
    live = live_module()
    def early_agent(model, runner, *, max_turns):
        return Agent(model, runner, max_turns=max_turns)
    monkeypatch.setattr(live, "Agent", early_agent)
    requests = []
    def handle(request):
        requests.append(json.loads(request.content))
        response = payload("openai-chat", len(requests) == 1)
        if len(requests) == 2:
            response["choices"][0]["message"]["content"] = "5"
        return httpx.Response(200, json=response)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = live.run_live(ModelConfig("openai-chat", "test", "https://fixture.invalid", "test-key"), client=client)
    assert result["passed"] and result["tool_calls"] == 1


def test_cli_env_field_and_explicit_override(monkeypatch, tmp_path):
    from minicode.cli import main
    from minicode.testing import FakeModel
    configs = []
    def factory(config):
        configs.append(config)
        model = FakeModel(["done"])
        model.source, model.close = {}, lambda: None
        return model
    monkeypatch.setattr("minicode.cli.create_model", factory)
    monkeypatch.setenv("MINICODE_API_KEY", "fixture-key")
    monkeypatch.setenv("MINICODE_CHAT_TOKEN_FIELD", "max_tokens")
    args = ["--api", "openai-chat", "--model", "test", "--cwd", str(tmp_path), "-p", "hello"]
    assert main(args) == 0
    assert configs[-1].chat_token_parameter == "max_tokens"
    assert main(args + ["--chat-token-field", "max_completion_tokens"]) == 0
    assert configs[-1].chat_token_parameter == "max_completion_tokens"
