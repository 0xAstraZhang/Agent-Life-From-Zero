"""编排消息、截止时间及工具；协议字段仍留在适配器。"""
from contextlib import contextmanager
from copy import copy
from dataclasses import asdict
from queue import Empty, Queue
import math
import signal
import threading
import time

from .types import Message, ModelReply, ToolResult, Usage, message_text, tool_calls
from .tool import ToolContext


class TaskTimeout(TimeoutError):
    """任务的墙钟时间已用完。"""


class _ParallelContext(ToolContext):
    """每批只读任务持有独立期限，取消后不受下一次查询改写影响。"""
    def __init__(self, original, deadline, cancelled):
        super().__init__(original.cwd, deadline)
        self.cancelled = cancelled

    def remaining(self, timeout):
        if self.cancelled.is_set():
            raise TaskTimeout("只读工具批次已取消")
        try:
            return super().remaining(timeout)
        except TimeoutError as error:
            raise TaskTimeout(str(error)) from error


def _with_context(runner, context):
    cloned = copy(runner)
    cloned.context = context
    if hasattr(runner, "inner"):
        cloned.inner = _with_context(runner.inner, context)
    return cloned


@contextmanager
def deadline_timer(deadline):
    """CLI 主线程用可恢复的计时器中断阻塞操作；其他线程靠下层 deadline。"""
    if deadline is None or threading.current_thread() is not threading.main_thread() or not hasattr(signal, "setitimer"):
        yield
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TaskTimeout("任务已达到时间上限")
    previous = signal.getitimer(signal.ITIMER_REAL)
    # 嵌套子 Agent 共享父任务期限；不替换更早到期的父计时器。
    if previous[0] and previous[0] <= remaining:
        yield
        return
    handler = signal.getsignal(signal.SIGALRM)
    started = time.monotonic()

    def expired(signum, frame):
        raise TaskTimeout("任务已达到时间上限")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, handler)
        if previous[0]:
            left = previous[0] - (time.monotonic() - started)
            if left > 0:
                signal.setitimer(signal.ITIMER_REAL, left, previous[1])


class Agent:
    def __init__(self, model, runner, system_prompt="你是一个编程助手。", *,
                 max_turns=20, history=None, stream=False, task_timeout=None,
                 deadline=None, parallel_tools=1, observer=None, agent_id="main"):
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 1
               for value in (max_turns, parallel_tools)):
            raise ValueError("max_turns 和 parallel_tools 必须是至少为 1 的整数")
        for name, value in (("task_timeout", task_timeout), ("deadline", deadline)):
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool)
                                      or not math.isfinite(value)):
                raise ValueError(f"{name} 必须是有限数值")
        if task_timeout is not None and task_timeout <= 0:
            raise ValueError("task_timeout 必须大于零")
        self.model, self.runner = model, runner
        self.max_turns, self.stream = max_turns, stream
        self.task_timeout, self.deadline = task_timeout, deadline
        self.parallel_tools, self.observer, self.agent_id = parallel_tools, observer, agent_id
        self.history = list(history) if history is not None else [Message.text("system", system_prompt)]
        self.usage = Usage()

    def _observe(self, event):
        if self.observer:
            self.observer({"agent_id": self.agent_id, **event})

    def _check_deadline(self):
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise TaskTimeout("任务已达到时间上限")

    def run(self, prompt: str):
        """一个用户任务，正常消费时以唯一的 result 事件结束。"""
        self.repair_pending()
        self.history.append(Message.text("user", prompt))
        prior_deadline = self.deadline
        model_deadline = getattr(self.model, "deadline", None)
        context_deadline = getattr(self.runner.context, "deadline", None)
        candidates = [value for value in (prior_deadline, model_deadline, context_deadline) if value is not None]
        if self.task_timeout is not None:
            candidates.append(time.monotonic() + self.task_timeout)
        self.deadline = min(candidates) if candidates else None
        setter = getattr(self.model, "set_deadline", None)
        if setter:
            setter(self.deadline)
        self.runner.context.deadline = self.deadline
        started, turn = time.monotonic(), 0
        try:
            self._observe({"type": "agent_start", "deadline": self.deadline})
            for turn in range(1, self.max_turns + 1):
                self._check_deadline()
                try:
                    reply = yield from self._request()
                    self._check_deadline()
                    if not isinstance(reply, ModelReply) or reply.message.role != "assistant":
                        raise ValueError("模型必须返回 assistant ModelReply")
                except TaskTimeout:
                    raise
                except Exception as error:
                    self._check_deadline()
                    yield self._result("error", f"模型请求失败：{type(error).__name__}: {error}", turn)
                    return
                self.usage.input_tokens += reply.usage.input_tokens
                self.usage.output_tokens += reply.usage.output_tokens
                text = message_text(reply.message)
                calls = tool_calls(reply.message)
                if reply.stop_reason not in {"stop", "tool_calls"}:
                    if text:
                        self.history.append(Message.text("assistant", text))
                    yield {"type": "assistant", "message": Message.text("assistant", text),
                           "text": text, "tool_calls": []}
                    status = reply.stop_reason if reply.stop_reason in {"length", "refusal", "cancelled"} else "error"
                    yield self._result(status, reply.error or text or f"模型停止：{reply.stop_reason}", turn)
                    return
                if bool(calls) != (reply.stop_reason == "tool_calls"):
                    yield self._result("error", "模型停止原因与工具调用不一致", turn)
                    return
                prior_ids = {call.id for message in self.history for call in tool_calls(message)}
                ids = [call.id for call in calls]
                if any(not value for value in ids) or len(set(ids)) != len(ids) or prior_ids.intersection(ids):
                    yield self._result("error", "模型返回了缺失或重复的工具调用 ID", turn)
                    return
                self.history.append(reply.message)
                yield {"type": "assistant", "message": reply.message, "text": text, "tool_calls": calls}
                if reply.stop_reason == "stop":
                    yield self._result("success", text, turn)
                    return
                for result in self._run_calls(calls):
                    self.history.append(Message("tool", [result]))
                    yield self._tool_event(result)
                self._check_deadline()
            yield self._result("max_turns", f"已达到最大轮数 {self.max_turns}", turn)
        except TaskTimeout:
            for result in self.repair_pending("任务超时，调用结果未知。请先检查现状，不要直接重做。"):
                yield self._tool_event(result)
            yield self._result("timeout", "任务已达到时间上限", turn)
        except KeyboardInterrupt:
            for result in self.repair_pending():
                yield self._tool_event(result)
            yield self._result("cancelled", "用户中断了任务", turn)
        finally:
            self.repair_pending()
            self.runner.context.deadline = context_deadline
            if setter:
                setter(model_deadline)
            self.deadline = prior_deadline
            self._observe({"type": "agent_end", "duration_seconds": time.monotonic() - started})

    def _parallel_safe(self, call):
        tool = self.runner.tools.get(call.name)
        if not tool or not tool.read_only or not tool.parallel_safe:
            return False
        # hook 可能改写调用或执行其他副作用；未声明线程安全的 wrapper 保持串行。
        current = self.runner
        while current is not None:
            if getattr(current, "before", None) is not None or getattr(current, "after", None) is not None:
                return False
            current = getattr(current, "inner", None)
        return True

    def _run_one(self, call, *, runner=None, limit=None, observe=None):
        runner = runner or self.runner
        check = limit or self._check_deadline
        observe = observe or self._observe
        check()
        started = time.monotonic()
        observe({"type": "tool_start", "call_id": call.id, "tool": call.name})
        result = None
        try:
            # 必须经过原 runner，保留权限、参数校验和 hook。
            with deadline_timer(getattr(runner.context, "deadline", None)):
                result = runner.run(call)
            check()
            return result
        except TaskTimeout:
            raise
        except Exception as error:
            check()
            result = ToolResult(call.id, f"工具执行层失败：{type(error).__name__}: {error}", True)
            return result
        finally:
            observe({"type": "tool_end", "call_id": call.id, "tool": call.name,
                     "duration_seconds": time.monotonic() - started,
                     "is_error": result.is_error if result is not None else True})

    def _parallel_group(self, calls):
        """只读批次，最多 N 个 daemon worker；结果仍按模型声明顺序回传。"""
        pending, completed = Queue(), Queue()
        stopped = threading.Event()
        observation_lock = threading.Lock()
        context = _ParallelContext(self.runner.context, self.deadline, stopped)
        runner = _with_context(self.runner, context)

        def observe(event):
            # 与finally的取消同步；parent结束后不会再向下一query写迟到日志。
            with observation_lock:
                if not stopped.is_set():
                    self._observe(event)

        for index, call in enumerate(calls):
            pending.put((index, call))

        def worker():
            while not stopped.is_set():
                try:
                    index, call = pending.get_nowait()
                except Empty:
                    return
                try:
                    value = self._run_one(call, runner=runner, limit=context.check_deadline, observe=observe)
                except BaseException as error:
                    value = error
                completed.put((index, value))

        workers = [threading.Thread(target=worker, daemon=True, name="minicode-read")
                   for _ in range(min(len(calls), self.parallel_tools))]
        for thread in workers:
            thread.start()
        outputs, next_index = {}, 0
        try:
            while next_index < len(calls):
                self._check_deadline()
                try:
                    index, value = completed.get(timeout=0.05)
                except Empty:
                    continue
                outputs[index] = value
                while next_index in outputs:
                    result = outputs.pop(next_index)
                    if isinstance(result, BaseException):
                        raise result
                    yield result
                    next_index += 1
        finally:
            with observation_lock:
                stopped.set()
            # 不合作的扩展仍可能继续读；其上下文已取消，且不能写入后续query的日志。
            for thread in workers:
                thread.join(timeout=0)

    def _run_calls(self, calls):
        index = 0
        while index < len(calls):
            self._check_deadline()
            if self.parallel_tools > 1 and self._parallel_safe(calls[index]):
                end = index + 1
                while end < len(calls) and self._parallel_safe(calls[end]):
                    end += 1
                if end - index > 1:
                    yield from self._parallel_group(calls[index:end])
                    index = end
                    continue
            yield self._run_one(calls[index])
            index += 1

    def _request(self):
        if not self.stream:
            with deadline_timer(self.deadline):
                return self.model.complete(self.history, self.runner.specs())
        reply = None
        stream = iter(self.model.stream(self.history, self.runner.specs()))
        try:
            while True:
                try:
                    with deadline_timer(self.deadline):
                        event = next(stream)
                except StopIteration:
                    break
                self._check_deadline()
                if reply is not None:
                    raise ValueError("完成事件之后仍收到流数据")
                if event.type == "done":
                    if event.reply is None:
                        raise ValueError("完成事件缺少最终回复")
                    reply = event.reply
                elif event.type == "text_delta":
                    yield {"type": "text_delta", "text": event.text}
                elif event.type == "tool_call_delta":
                    yield {"type": "tool_call_delta", "call_id": event.call_id,
                           "arguments_delta": event.arguments_delta}
                else:
                    raise ValueError(f"未知流事件：{event.type}")
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
        if reply is None:
            raise ValueError("流在完成事件之前结束")
        return reply

    def repair_pending(self, note="调用被中断，结果未知。请先检查现状，不要假定操作没有发生。"):
        answered = {part.call_id for message in self.history for part in message.content
                    if isinstance(part, ToolResult)}
        results = []
        for message in list(self.history):
            for call in tool_calls(message):
                if call.id not in answered:
                    result = ToolResult(call.id, note, True)
                    self.history.append(Message("tool", [result]))
                    answered.add(call.id)
                    results.append(result)
        return results

    @staticmethod
    def _tool_event(result):
        return {"type": "tool_result", "result": result, "call_id": result.call_id,
                "content": result.content, "is_error": result.is_error}

    def _result(self, status, text, turns):
        return {"type": "result", "status": status, "text": text,
                "turns": turns, "usage": asdict(self.usage)}
