"""Validate JSON-mode output and measurement fields before using them."""

import json
import math
import os

from openai import OpenAI

SYSTEM = """从用户的实验记录里抽取测量数据，只输出 json，不要任何解释。
示例输入：样本A的OD600是0.82
示例输出：{"sample": "A", "measure": "OD600", "value": 0.82}"""


def extract(text: str, retrys: int = 2) -> dict:
    """Try once plus `retrys` retries; API/transport errors propagate to the caller."""
    if not isinstance(retrys, int) or isinstance(retrys, bool) or retrys < 0:
        raise ValueError("retrys 必须是非负整数")
    api_key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not api_key or not model:
        raise ValueError("请先设置 LLM_API_KEY 和 LLM_MODEL（兼容服务还需 LLM_BASE_URL）。")
    client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=api_key)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    for attempt in range(1, retrys + 2):
        resp = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_object"}, max_tokens=500,
        )
        choice = resp.choices[0]
        if choice.finish_reason != "stop":
            print(f"第{attempt}次：输出未正常完成 -> {choice.finish_reason}")
            continue
        raw = choice.message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"第{attempt}次：不是合法JSON -> {raw!r}")
            continue
        if (
            isinstance(data, dict)
            and isinstance(data.get("sample"), str)
            and isinstance(data.get("measure"), str)
            and type(data.get("value")) in (int, float)
            and (isinstance(data["value"], int) or math.isfinite(data["value"]))
        ):
            return data
        print(f"第{attempt}次：字段不对 -> {data}")
    raise ValueError("模型多次没有给出符合要求的JSON")


if __name__ == "__main__":
    print(extract("今天测了一下，样本B的OD600读数是1.37"))
