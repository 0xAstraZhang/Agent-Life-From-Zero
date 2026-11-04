"""第 00 章：不联网，观察一次模型请求和回复。"""
from minicode.testing import FakeModel
from minicode.types import Message, message_text


def main():
    model = FakeModel(["你好，我是 minicode。"])
    reply = model.complete([Message.text("user", "你好")])
    print(message_text(reply.message))
    print("停止原因：", reply.stop_reason)
    print("模型请求次数：", len(model.requests))


if __name__ == "__main__":
    main()
