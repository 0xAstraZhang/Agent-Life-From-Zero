"""第 15 章验收，不导入第 16 章 CLI。"""
from minicode.evals import run_evals


def test_evals_check_real_files_and_expected_denials():
    assert all(result["passed"] for result in run_evals())
