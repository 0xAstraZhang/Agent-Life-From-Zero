"""第 03 章起共用的实验入口：默认真实模型，--offline 仅用于回归。"""
import argparse
from contextlib import contextmanager


def parse_args(description, argv=None, *, default_api="openai-chat",
               steps=None, default_step="minimal"):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--offline", action="store_true", help="使用确定的回复检查程序，不发送模型请求")
    parser.add_argument("--api", default=default_api,
                        choices=("openai-chat", "openai-responses", "anthropic-messages"))
    parser.add_argument("--step", choices=steps or ("minimal", "enhanced"), default=default_step)
    return parser.parse_args(argv)


@contextmanager
def open_model(args, script=()):
    """沿用同一组环境配置；离线夹具只替换模型，不替换工具与验收。"""
    if args.offline:
        from minicode.testing import FakeModel
        yield FakeModel(script)
        return

    from examples.live_model import config_from_env, parser
    from minicode.providers import create_model

    config = config_from_env(parser().parse_args(["--api", args.api]))
    model = create_model(config)
    try:
        yield model
    finally:
        model.close()


def print_event(event):
    """把学习时需要观察的工具结果、增量和最终回复显示出来。"""
    if event["type"] == "text_delta":
        print(event.get("text", ""), end="", flush=True)
    elif event["type"] == "tool_result":
        print("工具结果：", event.get("content", ""))
    elif event["type"] == "result":
        print(event.get("status", ""), event.get("text", ""))


def run_events(events):
    """显示事件并要求任务正常结束；各章继续验收自己的工具效果。"""
    collected = []
    for event in events:
        print_event(event)
        collected.append(event)
    final = next((event for event in reversed(collected) if event["type"] == "result"), None)
    if final is None:
        raise RuntimeError("没有收到任务结束结果")
    if final["status"] != "success":
        raise RuntimeError(f"任务未完成：{final['status']} {final.get('text', '')}")
    return collected
