"""The live smoke skips a toolchain that is on PATH but cannot run (a rustup shim with no toolchain)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _smoke():
    spec = importlib.util.spec_from_file_location("live_smoke", ROOT / "scripts" / "live_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_tool_usable_distinguishes_a_working_tool_from_a_shim(tmp_path: Path):
    smoke = _smoke()
    ok, why = smoke.tool_usable(sys.executable)
    assert ok and why == ""
    # A script that exists but exits non-zero on --version is a shim with nothing behind it.
    shim = tmp_path / "cargo.py"
    shim.write_text("import sys; sys.stderr.write('error: rustup could not choose a version of cargo to run\\n'); sys.exit(1)\n", encoding="utf-8")
    ok, why = smoke.tool_usable(str(shim))  # a .py path is not executable by itself: OSError path
    assert not ok and why
    ok, why = smoke.tool_usable(str(tmp_path / "missing-tool"))
    assert not ok and why
