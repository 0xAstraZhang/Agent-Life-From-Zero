"""第 18 章：每个 --step 只引入一种运行能力。"""
from dataclasses import asdict, replace
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from tempfile import TemporaryDirectory
import time

import httpx

from lessons.runtime import open_model, print_event
from minicode.agent import Agent
from minicode.coding_tools import build_coding_tools
from minicode.compact import CompactingModel
from minicode.observability import RunRecorder
from minicode.permissions import PermissionRunner
from minicode.processes import ProcessManager
from minicode.testing import call
from minicode.tool import Tool, ToolContext, ToolRunner
from minicode.types import Message, ToolResult, ToolSpec, message_text, tool_calls


def completed_text(reply):
    return reply.stop_reason == "stop" and not tool_calls(reply.message) and bool(message_text(reply.message).strip())


class TimedModel:
    """第一步只记录一次成功请求的时长和用量。"""
    def __init__(self, inner, record):
        self.inner, self.record = inner, record
        self.count = 0

    def complete(self, messages, tools=()):
        started = time.monotonic()
        reply = self.inner.complete(messages, tools)
        self.count += 1
        self.record({"type": "request_end", "local_request_id": f"lesson-{self.count}",
                     "seconds": time.monotonic() - started,
                     "normalized_usage": asdict(reply.usage),
                     "usage": getattr(reply, "usage_details", {})})
        return reply


class RetryBeforeSend:
    """只重试连接尚未建立的非流式请求，最多再尝试一次。"""
    def __init__(self, inner, delay=0.1):
        self.inner, self.delay, self.retries = inner, delay, 0

    def complete(self, messages, tools=()):
        try:
            return self.inner.complete(messages, tools)
        except httpx.ConnectError:
            self.retries += 1
            time.sleep(self.delay)
            return self.inner.complete(messages, tools)


def long_task_history():
    history = [Message.text("system", "保留用户的目标。"), Message.text("user", "找出 answer 的值。")]
    for i in range(4):
        request = call("Read", path=f"notes-{i}.txt")
        history.append(Message("assistant", [request]))
        history.append(Message("tool", [ToolResult(request.id, "已核对的记录。" * 80)]))
    return history


def compact_once(model):
    history = long_task_history()
    model = CompactingModel(model, threshold_chars=300, keep_turns=1)
    original_length = len(history)
    reply = model.complete(history)
    print("压缩次数：", model.compactions)
    print("完整历史条数：", len(history))
    print(message_text(reply.message))
    return model.compactions == 1 and len(history) == original_length and completed_text(reply)


def process_pages():
    """固定的本地进程，用两页输出观察日志游标，不需要模型。"""
    with TemporaryDirectory() as directory, ProcessManager() as manager:
        context = ToolContext(Path(directory))
        command = shlex.join([sys.executable, "-c", "print('first'); print('second')"])
        session = manager.start(command, context, timeout=5, interactive=False)
        result = manager.poll(session.id, context, wait=5, limit=6)
        print(result.content)
        second = manager.read_log(session.id, context, offset=6, limit=20)
        print(second.content)
        return (not result.is_error and not second.is_error
                and "first" in result.content and "second" in second.content)


def recorded_agent(model, root, recorder=None, *, task_timeout=None):
    (root / "answer.py").write_text("# example\nanswer = 42\n", encoding="utf-8")
    runner = PermissionRunner(
        ToolRunner(build_coding_tools(include_bash=False), ToolContext(root)), read_only=True)
    options = {}
    if recorder is not None:
        options["observer"] = recorder.record
    if task_timeout is not None:
        options["task_timeout"] = task_timeout
    final, read_ok, read_ids = None, False, set()
    for event in Agent(model, runner, max_turns=4, **options).run(
            "必须用 Read 读取 answer.py 的第二行（offset=2，limit=1），再回答 answer 的值。"):
        print_event(event)
        if event["type"] == "assistant":
            read_ids.update(c.id for c in event.get("tool_calls", [])
                            if c.name == "Read" and isinstance(c.arguments, dict)
                            and c.arguments.get("path") == "answer.py"
                            and c.arguments.get("offset") == 2 and c.arguments.get("limit") == 1
                            and c.arguments.get("column", 0) == 0)
        if event["type"] == "tool_result" and event["call_id"] in read_ids:
            read_ok = read_ok or (not event["is_error"] and "answer = 42" in event["content"]
                                  and "# example" not in event["content"])
        if event["type"] == "result":
            final = event
    if final is not None:
        final = {**final, "checkpoint_passed": read_ok and final["status"] == "success"}
    print("第二行读取验收：", read_ok)
    return final


def deadline_demo(model, root, seconds, *, expect_timeout=False):
    entered = []

    def wait(arguments, context):
        entered.append(True)
        time.sleep(0.05)
        context.check_deadline()
        return "等待结束"

    spec = ToolSpec("Wait", "等待 0.05 秒后返回", {"type": "object", "properties": {}, "additionalProperties": False})
    runner = ToolRunner([Tool(spec, wait, read_only=True)], ToolContext(root))
    final = None
    for event in Agent(model, runner, max_turns=3, task_timeout=seconds).run("必须调用 Wait 工具一次，然后回答完成。"):
        print_event(event)
        if event["type"] == "result":
            final = event
    expected = "timeout" if expect_timeout else "success"
    passed = bool(entered) and final is not None and final["status"] == expected
    print("期限实验预期：", expected, "验收：", passed)
    return passed


def main(argv=None):
    parser = argparse.ArgumentParser(description="一次只增加一种运行能力")
    parser.add_argument("--step", choices=("minimal", "retry", "deadline", "context", "processes", "enhanced"), default="minimal")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--api", choices=("openai-chat", "openai-responses", "anthropic-messages"),
                        default=os.getenv("MINICODE_API", "openai-chat"))
    parser.add_argument("--task-timeout", type=float, help="deadline 实验的任务期限，单位秒")
    args = parser.parse_args(argv)
    if args.step == "processes":
        return 0 if process_pages() else 1
    script = ["你好，运行记录。"]
    if args.step == "retry":
        script = [httpx.ConnectError("离线注入：连接未建立"), "连接恢复后得到回复。"]
    elif args.step == "context":
        script = ["目标：找出 answer；已核对四份记录。", "仍需读取包含 answer 的文件。"]
    elif args.step == "deadline":
        script = [call("Wait"), "等待完成"]
    elif args.step == "enhanced":
        script = [call("Read", path="answer.py", offset=2, limit=1), "answer = 42"]
    with open_model(args, script) as model:
        if args.step == "minimal":
            records = []
            reply = TimedModel(model, records.append).complete([Message.text("user", "你好，用一句话回复。")])
            print(message_text(reply.message))
            print(json.dumps(records, ensure_ascii=False))
            return 0 if completed_text(reply) else 1
        if args.step == "retry":
            if hasattr(model, "config"):
                model.config = replace(model.config, max_retries=0)
            wrapper = RetryBeforeSend(model)
            reply = wrapper.complete([Message.text("user", "你好，用一句话回复。")])
            print(message_text(reply.message))
            print("连接重试次数：", wrapper.retries)
            return 0 if completed_text(reply) else 1
        if args.step == "context":
            return 0 if compact_once(model) else 1
        with TemporaryDirectory() as directory:
            root = Path(directory)
            if args.step == "deadline":
                seconds = args.task_timeout if args.task_timeout is not None else (0.01 if args.offline else 60)
                return 0 if deadline_demo(model, root, seconds, expect_timeout=args.offline) else 1
            else:
                recorder = RunRecorder(root / "run.jsonl")
                try:
                    if hasattr(model, "observer"):
                        model.observer = recorder.record
                    else:
                        model = TimedModel(model, recorder.record)
                    final = recorded_agent(model, root, recorder)
                    print(json.dumps(recorder.summary(), ensure_ascii=False))
                finally:
                    recorder.close()
                print("运行记录条数：", len((root / "run.jsonl").read_text().splitlines()))
            return 0 if final and final["checkpoint_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
