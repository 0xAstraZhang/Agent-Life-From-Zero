"""第 00 章：直接请求 Chat Completions；--offline 只用于离线检查。"""
import argparse
from contextlib import contextmanager
import json
import os

import httpx


class ChatModel:
    def __init__(self, client, model, base_url):
        self.client = client
        self.model = model
        self.base_url = base_url.rstrip("/")

    def complete(self, messages, tools=()):
        body = {"model": self.model, "messages": messages}
        if tools:
            body["tools"] = tools
        response = self.client.post(self.base_url + "/chat/completions", json=body)
        response.raise_for_status()
        return response.json()["choices"][0]


def chat_once(model, prompt):
    messages = [{"role": "user", "content": prompt}]
    reply = model.complete(messages)
    return completed_text(reply)


def chat_twice(model, first_prompt, second_prompt):
    messages = [{"role": "user", "content": first_prompt}]
    first = model.complete(messages)
    completed_text(first)
    messages.append(first["message"])
    messages.append({"role": "user", "content": second_prompt})
    second = model.complete(messages)
    return completed_text(second)


def completed_text(reply):
    if reply["finish_reason"] != "stop" or reply["message"].get("tool_calls"):
        raise RuntimeError("模型还没有完成这次文本回答")
    text = reply["message"].get("content")
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("模型没有返回可见文本")
    return text


def lesson_args(description, argv=None):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--offline", action="store_true", help="使用固定 HTTP 响应检查代码，不请求模型")
    parser.add_argument("--step", choices=("minimal", "enhanced"), default="minimal")
    return parser.parse_args(argv)


def environment_config(environ=None):
    env = os.environ if environ is None else environ
    fields = ("MODEL", "BASE_URL", "API_KEY")
    prefix = "MINICODE" if any(env.get(f"MINICODE_{field}") for field in fields) else "LLM"
    missing = [f"{prefix}_{field}" for field in fields if not env.get(f"{prefix}_{field}")]
    if missing:
        raise ValueError("请在同一组环境变量中设置：" + ", ".join(missing))
    if env.get(f"{prefix}_API", "openai-chat") != "openai-chat":
        raise ValueError("00–02 章使用 Chat Completions，请把所选配置组的 API 设为 openai-chat")
    return {"model": env[f"{prefix}_MODEL"], "base_url": env[f"{prefix}_BASE_URL"],
            "api_key": env[f"{prefix}_API_KEY"]}


@contextmanager
def open_chat(*, offline=False, replies=()):
    if offline:
        # 固定响应仅服务于显式的离线检查，教学入口默认连接真实 API。
        pending = iter(replies)

        def serve(request):
            json.loads(request.content)  # 仍然发送并解析真实 HTTP 请求体。
            return httpx.Response(200, json={"choices": [next(pending)]})

        with httpx.Client(transport=httpx.MockTransport(serve)) as client:
            yield ChatModel(client, "offline-fixture", "https://fixture.invalid/v1")
    else:
        config = environment_config()
        with httpx.Client(headers={"Authorization": f"Bearer {config['api_key']}"}, timeout=60) as client:
            yield ChatModel(client, config["model"], config["base_url"])


def text_reply(text):
    """离线检查用的协议响应，不参与真实请求。"""
    return {"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}


def main(argv=None):
    args = lesson_args(__doc__, argv)
    replies = [text_reply("你好，我是 minicode。")]
    if args.step == "enhanced":
        replies = [text_reply("我记住了：小林。"), text_reply("你叫小林。")]
    with open_chat(offline=args.offline, replies=replies) as model:
        if args.step == "minimal":
            print(chat_once(model, "你好！请用一句话介绍你自己。"))
        else:
            print(chat_twice(model, "我叫小林，请记住我的名字。", "我叫什么名字？"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
