import os
from openai import OpenAI

client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))
MODEL = os.getenv("LLM_MODEL")

messages = [{"role": "system", "content": "你是一个友好的、用词简练的助手，每次回答不超过三句话。"}]

while True:
    user_input = input("You: ")
    if user_input in {"exit", "quit"}:
        break
    messages.append({"role": "user", "content": user_input})

    resp = client.chat.completions.create(model=MODEL, messages=messages)
    reply = resp.choices[0].message.content

    messages.append({"role": "assistant", "content": reply})
    print("DeepSeek: ", reply)

print(f"(finish_reason={resp.choices[0].finish_reason}, tokens={resp.usage.total_tokens})")