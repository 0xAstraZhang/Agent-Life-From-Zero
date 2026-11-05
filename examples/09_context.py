"""第 09 章：真实模型读取项目约定；--offline 运行确定性回归。"""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory

from lessons.ch09 import project_prompt
from lessons.runtime import open_model, parse_args, run_events
from minicode.agent import Agent
from minicode.context import build_system_prompt, load_project_instructions
from minicode.tool import ToolContext, ToolRunner
from minicode.types import message_text


def main(argv=None):
    args = parse_args("第 09 章：给模型加入项目约定", argv)
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "MINICODE.md").write_text("回答以‘项目约定：’开头。测试命令是 python -m pytest。", encoding="utf-8")
        if args.step == "minimal":
            prompt = project_prompt(root)
        else:
            prompt = build_system_prompt(root, load_project_instructions(root))
        print("发送的 system：", prompt)
        with open_model(args, ["项目约定：测试命令是 python -m pytest。"]) as model:
            requests = []

            class ObservedModel:
                def complete(self, messages, tools=()):
                    requests.append(deepcopy(messages))
                    return model.complete(messages, tools)

            agent = Agent(ObservedModel(), ToolRunner([], ToolContext(root)), prompt)
            run_events(agent.run("这个项目应该怎样运行测试？"))
            instructions = (root / "MINICODE.md").read_text(encoding="utf-8")
            if not requests or instructions not in message_text(requests[0][0]):
                raise RuntimeError("模型请求没有收到项目约定")


if __name__ == "__main__":
    main()
