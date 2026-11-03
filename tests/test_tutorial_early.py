"""Run the complete Stage 1–3 tutorial blocks without paid API calls.

Extract into pytest's temporary directory so examples cannot touch user files.
"""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs"


def blocks(stage):
    doc = next(DOCS.glob(f"Stage {stage} *"))
    return re.findall(r"^```python\n(.*?)^```", doc.read_text(encoding="utf-8"), re.M | re.S)


def materialize(stage, directory, mapping):
    snippets = blocks(stage)
    for filename, prefix in mapping.items():
        matches = [s for s in snippets if s.startswith(prefix)]
        assert len(matches) == 1, (stage, filename)
        compile(matches[0], filename, "exec")
        (directory / filename).write_text(matches[0], encoding="utf-8")


def run_tests(directory, filename):
    env = os.environ.copy()
    for key in ("LLM_API_KEY", "OPENAI_API_KEY", "EMBED_MODEL", "EMBED_API_KEY", "TAVILY_API_KEY"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", filename], cwd=directory, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_stage1_offline_regressions(tmp_path):
    materialize(1, tmp_path, {"agent.py": '\"\"\"Stage 1 最小 agent'})
    (tmp_path / "test_agent.py").write_text('''
import agent
import pytest
from types import SimpleNamespace


def test_search_cannot_read_external_symlink(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("outside auditsecret", encoding="utf-8")
    (workspace / "alias.txt").symlink_to(outside)
    (workspace / "local.txt").write_text("local auditsecret", encoding="utf-8")
    monkeypatch.setattr(agent, "WORKDIR", workspace)
    assert "outside" not in agent.search("auditsecret")
    assert "local auditsecret" in agent.search("auditsecret")
    with pytest.raises(PermissionError):
        agent.read_file("alias.txt")


def test_calculator_and_bad_tool_arguments():
    assert agent.calculator("(0.82+1.37)/2") == "1.095"
    assert "错误" in agent.run_tool("calculator", "[]")
    assert "错误" in agent.run_tool("calculator", '{"expression":"1/0"}')


def test_fake_model_finishes_without_key():
    class Message:
        content = "done"
        tool_calls = None
        def model_dump(self, **kwargs):
            return {"role": "assistant", "content": self.content}
    class Client:
        def __init__(self):
            self.chat = self.completions = self
        def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=Message())])
    assert agent.run_agent("hello", client=Client(), verbose=False) == "done"
''', encoding="utf-8")
    run_tests(tmp_path, "test_agent.py")


def test_stage2_embedded_tests(tmp_path):
    materialize(2, tmp_path, {
        "rag.py": '\"\"\"RAG 的前三步',
        "tools.py": '\"\"\"把文件、搜索、网页',
        "memory.py": '\"\"\"三层记忆',
        "research_agent.py": '\"\"\"Stage 2 资料研究助手',
        "fake_llm.py": '\"\"\"按剧本回答的假模型',
        "test_research_agent.py": '\"\"\"不联网、不花钱的测试',
    })
    run_tests(tmp_path, "test_research_agent.py")


def test_stage3_embedded_tests(tmp_path):
    materialize(3, tmp_path, {
        "harness.py": '\"\"\"一个可调试的迷你 harness',
        "demo_tools.py": '\"\"\"演示用的工具',
        "demo.py": '\"\"\"运行 harness。',
        "fake_llm.py": '\"\"\"按剧本回答的假模型',
        "test_harness.py": '\"\"\"运行：uv run pytest test_harness.py',
    })
    run_tests(tmp_path, "test_harness.py")


def test_stage1_json_validation_retries(tmp_path):
    # Execute only imports and extract(), not the example's client or live call.
    import ast
    import json
    import math
    from types import SimpleNamespace

    source = next(s for s in blocks(1) if s.startswith("import json, math, os"))
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "extract")
    invalid = ["[]", "null", '{"sample":"A","value":1}',
               '{"sample":"A","measure":"OD600","value":true}',
               '{"sample":"A","measure":"OD600","value":NaN}',
               '{"sample":"A","measure":"OD600","value":Infinity}',
               '{"sample":"A","measure":"OD600","value":1e999}']
    for raw in invalid:
        class Client:
            calls = 0
            def __init__(self):
                self.chat = self.completions = self
            def create(self, **kwargs):
                self.calls += 1
                data = raw if self.calls == 1 else '{"sample":"A","measure":"OD600","value":1.37}'
                return SimpleNamespace(choices=[SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=data))])
        client = Client()
        namespace = {"json": json, "math": math, "client": client, "MODEL": "fake", "SYSTEM": "fake"}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "extract_json.py", "exec"), namespace)
        assert namespace["extract"]("test")["value"] == 1.37
        assert client.calls == 2


def test_complete_blocks_compile():
    # All full programs plus standalone snippets, excluding the intentionally
    # partial loop skeleton (return outside a function) and registry insertion.
    excluded = {1: ("client = OpenAI(base_url=BASE_URL",), 2: (), 3: ("# ① 在 TOOLS",)}
    for stage in (1, 2, 3):
        for index, snippet in enumerate(blocks(stage)):
            if snippet.startswith(excluded[stage]):
                continue
            compile(snippet, f"stage{stage}-block{index}", "exec")
