"""直接运行正文承诺可复制的检查点；HTTP 由测试替换，正文默认真实 API。"""
from contextlib import contextmanager
import json
from pathlib import Path
import re

import httpx
import pytest

from lessons.ch00 import ChatModel, text_reply
from lessons.ch01 import tool_reply

ROOT = Path(__file__).resolve().parents[1]
CHAPTERS = ("00-setup.md", "01-agent-loop.md", "02-tools.md", "03-chat-completions.md")


def checkpoint(chapter, step):
    blocks = re.findall(r"```python\n(.*?)\n```", (ROOT / "docs" / CHAPTERS[chapter]).read_text(), re.S)
    prefix = f"# 可运行检查点：{chapter:02}-{step}\n"
    matches = [block for block in blocks if block.startswith(prefix)]
    assert len(matches) == 1, (chapter, step)
    return matches[0]


@pytest.mark.parametrize("chapter", range(4))
@pytest.mark.parametrize("step", ["minimal", "enhanced"])
def test_document_checkpoint_runs_through_http(chapter, step, monkeypatch, capsys):
    if chapter == 0:
        replies = [text_reply("记住了"), text_reply("小林")] if step == "enhanced" else [text_reply("你好")]
    elif chapter == 2:
        replies = [tool_reply("add", {"a": 2, "b": 3}, "sum"),
                   tool_reply("multiply", {"a": 5, "b": 4}, "product"), text_reply("20")]
    else:
        replies = [tool_reply("add", {"a": 2, "b": 3}), text_reply("5")]
    requests = []
    pending = iter(replies)

    def serve(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [next(pending)]})

    @contextmanager
    def open_client(*args, **kwargs):
        with httpx.Client(transport=httpx.MockTransport(serve)) as client:
            if chapter < 3:
                yield ChatModel(client, "fixture", "https://fixture.invalid/v1")
            else:
                from minicode.providers import ModelConfig, create_model
                model = create_model(ModelConfig("openai-chat", "fixture", "https://fixture.invalid/v1", "fixture"), client)
                try:
                    yield model
                finally:
                    model.close()

    monkeypatch.setattr("lessons.ch00.open_chat", open_client)
    if chapter == 3:
        monkeypatch.setattr("lessons.runtime.open_model", open_client)
    exec(compile(checkpoint(chapter, step), str(ROOT / "docs" / CHAPTERS[chapter]), "exec"), {})
    assert len(requests) == len(replies)
    output = capsys.readouterr().out
    assert ("小林" if chapter == 0 and step == "enhanced" else "你好" if chapter == 0 else "20" if chapter == 2 else "5") in output
    if chapter > 0:
        results = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
        assert results[-1]["content"] == ("20" if chapter == 2 else "5")
        calls = {call["id"] for message in requests[-1]["messages"] for call in message.get("tool_calls", [])}
        assert all(message["tool_call_id"] in calls for message in results)


@pytest.mark.parametrize("step", ["minimal", "enhanced"])
def test_adapter_checkpoint_requires_local_execution(step, monkeypatch):
    import runpy
    from minicode.testing import FakeModel

    main = runpy.run_path(str(ROOT / "examples/03_chat_completions.py"))["main"]

    @contextmanager
    def direct_answer(*args, **kwargs):
        yield FakeModel(["5"])

    monkeypatch.setitem(main.__globals__, "open_model", direct_answer)
    with pytest.raises(RuntimeError):
        main(["--offline", "--step", step])


def test_mcp_bridge_from_document_completes_real_process_roundtrip(tmp_path):
    from lessons.ch14 import echo_process
    from minicode.tool import ToolContext, ToolRunner
    from minicode.types import ToolCall

    prose = (ROOT / "docs/14-mcp.md").read_text()
    blocks = re.findall(r"```python\n(.*?)\n```", prose, re.S)
    code = "\n\n".join(block for block in blocks
                       if "def request(process," in block or "def echo_tool(process):" in block)
    namespace = {}
    exec(compile(code, "docs/14-mcp.md", "exec"), namespace)
    with echo_process() as process:
        tool = namespace["echo_tool"](process)
        runner = ToolRunner([tool], ToolContext(tmp_path))
        result = runner.run(ToolCall("reader-call", tool.spec.name, {"text": "from the copied bridge"}))
    assert not result.is_error
    assert result.call_id == "reader-call" and result.content == "from the copied bridge"


def test_minimal_cli_document_is_a_complete_program(tmp_path, monkeypatch, capsys):
    import sys

    prose = (ROOT / "docs/16-shipping.md").read_text()
    code = next(block for block in re.findall(r"```python\n(.*?)\n```", prose, re.S)
                if block.startswith("import argparse\n"))
    (tmp_path / "sample.py").write_text("pass\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["mycli.py", "--offline", "-p", "列出 Python 文件"])
    namespace = {"__name__": "reader_example"}
    exec(compile(code, "docs/16-shipping.md", "exec"), namespace)
    assert namespace["main"]() == 0
    assert "sample.py" in capsys.readouterr().out
