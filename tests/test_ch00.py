"""第 00 章：真正经过 HTTP 编码，并验证第二次请求携带完整历史。"""
import json

import httpx
import pytest

from lessons.ch00 import ChatModel, chat_once, chat_twice, environment_config, open_chat, text_reply


def test_chat_once_sends_the_prompt_and_returns_response():
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, json={"choices": [text_reply("你好")]})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        assert chat_once(ChatModel(client, "fixture", "https://fixture.invalid/v1"), "打个招呼") == "你好"
    assert requests == [{"model": "fixture", "messages": [{"role": "user", "content": "打个招呼"}]}]


def test_chat_twice_sends_user_assistant_and_followup():
    requests = []

    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [text_reply("记住了" if len(requests) == 1 else "小林")]})

    with httpx.Client(transport=httpx.MockTransport(serve)) as client:
        answer = chat_twice(ChatModel(client, "fixture", "https://fixture.invalid"), "我叫小林", "我叫什么？")
    assert answer == "小林"
    assert requests[1]["messages"] == [
        {"role": "user", "content": "我叫小林"},
        {"role": "assistant", "content": "记住了"},
        {"role": "user", "content": "我叫什么？"},
    ]


def test_config_does_not_mix_sources_or_print_secret():
    env = {"MINICODE_MODEL": "new", "LLM_MODEL": "old", "LLM_API_KEY": "secret", "LLM_BASE_URL": "https://old"}
    with pytest.raises(ValueError) as error:
        environment_config(env)
    assert "MINICODE_API_KEY" in str(error.value) and "secret" not in str(error.value)
    assert environment_config({key: value for key, value in env.items() if key.startswith("LLM_")})["model"] == "old"


def test_truncated_answer_is_not_a_completed_conversation():
    reply = text_reply("回答到一半")
    reply["finish_reason"] = "length"
    with open_chat(offline=True, replies=[reply]) as model:
        with pytest.raises(RuntimeError, match="还没有完成"):
            chat_once(model, "问题")
