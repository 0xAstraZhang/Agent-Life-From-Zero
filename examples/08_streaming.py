"""第 8 章：三种 SSE 格式都产生统一增量与最终回复。"""
from minicode.providers.demo import run_demo

if __name__ == "__main__":
    for api in ("openai-chat", "openai-responses", "anthropic-messages"):
        run_demo(api, stream=True)
