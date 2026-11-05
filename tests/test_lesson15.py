from lessons.ch15 import evaluate, main
from minicode.testing import FakeModel


def test_completion_claim_cannot_replace_file_acceptance(tmp_path):
    report = evaluate(FakeModel(["完成"]), tmp_path)
    assert report["status"] == "success"
    assert not report["file_passed"]
    assert not report["passed"]


def test_file_task_and_budget_checkpoint():
    assert main(["--offline", "--step", "minimal"]) == 0
    assert main(["--offline", "--step", "enhanced"]) == 0
