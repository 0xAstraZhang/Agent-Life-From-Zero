import pytest

from lessons.ch17 import minimal
from minicode.sandbox import Sandbox, SandboxUnavailable


def test_minimal_mounts_restrict_real_writes(tmp_path):
    try:
        Sandbox().check(tmp_path)
    except SandboxUnavailable as error:
        pytest.skip(str(error))
    assert minimal() == 0
