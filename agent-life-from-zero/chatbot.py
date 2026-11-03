"""A minimal Chat Completions conversation (configure LLM_* first)."""

import os

from openai import OpenAI


def main() -> None:
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not api_key or not model:
        raise SystemExit("请先设置 LLM_API_KEY 和 LLM_MODEL（兼容服务还需 LLM_BASE_URL）。")
    client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=api_key)
    messages = [{"role": "system", "content": "你是一个友好的、用词简练的助手，每次回答不超过三句话。"}]

    while True:
        try:
            user_input = input("You: ")
        except (EOFError, KeyboardInterrupt):
            break
        if user_input.strip().lower() in {"exit", "quit"}:
            break
        messages.append({"role": "user", "content": user_input})

        resp = client.chat.completions.create(model=model, messages=messages)
        reply = resp.choices[0].message.content or ""
        messages.append({"role": "assistant", "content": reply})
        print("Assistant: ", reply)
        tokens = resp.usage.total_tokens if resp.usage is not None else "unknown"
        print(f"(finish_reason={resp.choices[0].finish_reason}, tokens={tokens})")


if __name__ == "__main__":
    main()
