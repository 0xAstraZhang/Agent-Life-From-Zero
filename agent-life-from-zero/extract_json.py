#Who is JSON?

import json, os
from openai import OpenAI

client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY"))
MODEL = os.getenv("LLM_MODEL", "deepseek-flash")
SYSTEM = """从用户的实验记录里抽取测量数据，只输出 json，不要任何解释。
示例输入：样本A的OD600是0.82
示例输出：{"sample": "A", "measure": "OD600", "value": 0.82}"""

def extract(text:str, retry:int = 2) -> dict:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}] 
    for attempt in range(0, retry + 1):
        resp = client.chat.completions.create(
            model=MODEL, messages=messages, 
            response_format={"type": "json_object"}, max_tokens=500
        )
        raw = resp.choices[0].message.content or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print(f"第{attempt}次：不是合法JSON -> {raw!r}")
            continue
        if isinstance(data.get("sample"), str) and isinstance(data.get("value"), (int, float)):
            return data
        print(f"第{attempt}次：字段不对 -> {data}")
    raise ValueError("模型多次没有给出符合要求的JSON")

print(extract("今天测了一下，样本B的OD600读数是1.37"))
