import re

from minicode.coding_tools import MAX_OUTPUT, build_coding_tools
from minicode.tool import ToolContext, ToolRunner
from minicode.types import ToolCall


def run(executor, name, **args):
    return executor.run(ToolCall("range", name, args))


def test_read_middle_page_keeps_original_line_numbers_and_freshness(tmp_path):
    target = tmp_path / "large.py"
    target.write_text("\n".join(f"value_{i} = {i}" for i in range(1, 601)))
    executor = ToolRunner(build_coding_tools(include_bash=False), ToolContext(tmp_path))
    result = run(executor, "Read", path="large.py", offset=300, limit=2)
    assert not result.is_error
    assert "300\tvalue_300 = 300" in result.content
    assert "301\tvalue_301 = 301" in result.content
    assert "value_299" not in result.content and "value_302" not in result.content
    assert "offset=302" in result.content
    assert not run(executor, "Edit", path="large.py", old="value_300 = 300", new="value_300 = 0").is_error
    target.write_text("externally changed")
    assert run(executor, "Edit", path="large.py", old="changed", new="overwritten").is_error


def test_read_can_resume_a_line_larger_than_output_budget(tmp_path):
    text = "前" * (MAX_OUTPUT * 2 + 37)
    (tmp_path / "one_line.txt").write_text(text)
    executor = ToolRunner(build_coding_tools(include_bash=False), ToolContext(tmp_path))
    column = 0
    recovered = []
    for _ in range(4):
        result = run(executor, "Read", path="one_line.txt", offset=1, column=column, limit=1)
        assert not result.is_error and len(result.content) <= MAX_OUTPUT
        recovered.append(result.content.splitlines()[0].split("\t", 1)[1])
        continuation = re.search(r"继续 Read offset=(\d+) column=(\d+)", result.content)
        if not continuation:
            break
        assert continuation.group(1) == "1"
        column = int(continuation.group(2))
    assert "".join(recovered) == text


def test_read_rejects_bad_ranges_and_grep_scopes_relative_paths(tmp_path):
    (tmp_path / "a.py").write_text("needle")
    (tmp_path / "b.txt").write_text("needle")
    executor = ToolRunner(build_coding_tools(include_bash=False), ToolContext(tmp_path))
    assert run(executor, "Read", path="a.py", offset=0).is_error
    assert run(executor, "Read", path="a.py", limit=0).is_error
    assert run(executor, "Read", path="a.py", column=99).is_error
    result = run(executor, "Grep", text="needle", path="*.py")
    assert "a.py:1:needle" in result.content and "b.txt" not in result.content
