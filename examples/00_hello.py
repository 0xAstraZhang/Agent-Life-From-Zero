"""第 00 章：一个文件即可运行，不依赖尚未编写的 minicode 包。"""
from copy import deepcopy


class ScriptedModel:
    def __init__(self):
        self.requests = []

    def complete(self, messages):
        self.requests.append(deepcopy(messages))
        return {"text": "你好，我是 minicode。", "stop_reason": "stop"}


def main():
    model = ScriptedModel()
    reply = model.complete([{"role": "user", "text": "你好"}])
    print(reply["text"])
    print("停止原因：", reply["stop_reason"])
    print("模型请求次数：", len(model.requests))


if __name__ == "__main__":
    main()
