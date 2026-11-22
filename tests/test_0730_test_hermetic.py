"""The autouse isolate fixture must not inherit a leftover CLAIMIDX_* path from the shell."""

from __future__ import annotations

import os
from pathlib import Path

from claimidx.env import last_failure_path


def test_last_failure_and_db_paths_stay_under_tmp_path(tmp_path: Path):
    lf = last_failure_path()
    assert lf == tmp_path / "last_failure.json"
    assert os.environ["CLAIMIDX_LAST_FAILURE"] == str(lf)
    assert os.environ["CLAIMIDX_DB"] == str(tmp_path / "index.sqlite")
    assert os.environ["CLAIMIDX_VERIFY_SEEN"] == str(tmp_path / "verify-seen.json")
