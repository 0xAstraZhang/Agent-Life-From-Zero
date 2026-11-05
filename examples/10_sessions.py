"""第 10 章：保存恢复与请求压缩分别运行。"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.ch10 import compact_history, load_history, save_history
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.compact import CompactingModel
from minicode.session import SessionStore, run_session
from minicode.tool import ToolContext, ToolRunner
from minicode.types import Message, message_text


def main(argv=None):
    args = parse_args("第 10 章：保存恢复，或单独观察压缩", argv,
                      steps=("minimal", "enhanced", "compact", "compact-enhanced"))
    script = ["已记住：项目代号是星河，测试命令是 python -m pytest。"]
    if args.step.startswith("compact"):
        script += ["项目星河：使用 python -m pytest 运行测试。", "星河项目使用 python -m pytest。"]
    else:
        script += ["星河项目使用 python -m pytest。"]
    with TemporaryDirectory() as directory, open_model(args, script) as model:
        root = Path(directory)
        runner = ToolRunner([], ToolContext(root))
        first = Agent(model, runner)
        run_events(first.run("请记住：项目代号是星河，测试命令是 python -m pytest。"))
        saved_history = deepcopy(first.history)
        question = "项目代号和测试命令分别是什么？"
        if args.step == "minimal":
            path = root / "history.json"
            save_history(path, first.history)
            del first
            history = load_history(path)
            if history != saved_history:
                raise RuntimeError("从磁盘恢复的历史与保存内容不同")
            print(f"从磁盘恢复 {len(history)} 条历史")
            resumed = Agent(model, runner, history=history)
            run_events(resumed.run(question))
            if resumed.history[:len(saved_history)] != saved_history:
                raise RuntimeError("继续任务时丢失了恢复的历史")
        elif args.step == "enhanced":
            store = SessionStore(root / "session.jsonl")
            store.save(first.history)
            del first
            history = store.load()
            if history != saved_history:
                raise RuntimeError("从会话文件恢复的历史与保存内容不同")
            resumed = Agent(model, runner, history=history)
            run_events(run_session(resumed, question, store))
            if store.load() != resumed.history or resumed.history[:len(saved_history)] != saved_history:
                raise RuntimeError("续轮后会话没有完整保存")
            print(f"会话已保存 {len(store.load())} 条历史")
        elif args.step == "compact":
            first.history.append(Message.text("user", question))
            original = deepcopy(first.history)
            request = compact_history(model, first.history)
            if first.history != original or request[-1] != original[-1]:
                raise RuntimeError("压缩改动了原历史或最新问题")
            print("摘要后的请求：", [(message.role, message_text(message)) for message in request])
            reply = model.complete(request, [])
            if reply.stop_reason != "stop" or not message_text(reply.message).strip():
                raise RuntimeError(f"模型未完成回答：{reply.stop_reason}")
            print(message_text(reply.message))
            print(f"原历史 {len(first.history)} 条，请求视图 {len(request)} 条")
        else:
            compressed = CompactingModel(model, threshold_chars=1, keep_turns=1)
            continued = Agent(compressed, runner, history=first.history)
            run_events(continued.run(question))
            if compressed.compactions < 1 or continued.history[:len(saved_history)] != saved_history:
                raise RuntimeError("压缩未发生，或原始历史被改动")
            print(f"摘要次数：{compressed.compactions}；完整历史：{len(continued.history)} 条")


if __name__ == "__main__":
    main()
