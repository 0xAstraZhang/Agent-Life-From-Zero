from pathlib import Path
import runpy


def test_first_interaction_is_standalone(capsys):
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/00_hello.py"), run_name="__main__")
    output = capsys.readouterr().out
    assert "你好" in output and "stop" in output
