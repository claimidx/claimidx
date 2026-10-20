"""Audit polish: replay / sandbox / runwrap / apply / claim / env."""

from __future__ import annotations

import sys
from pathlib import Path

import claimidx.policy as policy
import claimidx.replay as replay_mod
import claimidx.sandbox as sandbox
from claimidx.fingerprint import classify, fingerprint, normalize_error
from claimidx.hook import _first_err_line
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.replay import _apply_pin_and_replay, decide, pick
from claimidx.runwrap import after_run, run_command
from claimidx.sandbox import ReplayResult, replay
from claimidx.store import Store


def _claim(err: str, eval_cmd: str, *, fix_k="pin", fix_b="demo==1", st="proposed") -> Claim:
    cls = classify(err)
    return Claim(
        fp=fingerprint(err=err, cls=cls, eco="py", rt="py@3.11", dep=["demo@1"]),
        cls=cls,
        err=normalize_error(err),
        eco="py",
        rt="py@3.11",
        dep=["demo@1"],
        fix=Fix(k=fix_k, b=fix_b),
        eval=EvalSpec(cmd=eval_cmd),
        st=st,
        own="did:claimidx:test",
    )


# 1. _apply_pin_and_replay: no throwaway replay() before the venv-python run.


def test_apply_pin_and_replay_runs_no_throwaway_replay(tmp_path: Path, monkeypatch):
    replays = {"n": 0}

    def counting_replay(*a, **k):
        replays["n"] += 1
        return ReplayResult(True, True, 1, 0, False, "eval-miss")

    monkeypatch.setattr("claimidx.replay.replay", counting_replay)

    class R:
        def __init__(self, rc, stderr=""):
            self.returncode = rc
            self.stderr = stderr
            self.stdout = ""

    runs: list[list[str]] = []

    def fake_run(argv, *a, **k):
        runs.append(list(argv))
        if "venv" in argv or "pip" in argv:
            return R(0)
        return R(1, "ModuleNotFoundError: No module named 'demo'\n")

    monkeypatch.setattr("claimidx.replay.subprocess.run", fake_run)
    c = _claim("ModuleNotFoundError: No module named 'demo'", 'python -c "import demo"', fix_k="pin", fix_b="demo<2")
    d = _apply_pin_and_replay(c, tmp_path)
    assert d is not None and d["action"] == "fail" and d["reason"] == "eval-miss-pin"
    # venv, pip install, then exactly one eval run under the venv interpreter — no extra sandbox replay.
    assert replays["n"] == 0
    assert len(runs) == 3 and runs[-1][1:] == ["-c", "import demo"]


# 2. One tautology grammar: public.eval_is_proof decides what pick/decide may replay.


def test_pick_skips_version_hints_public_treats_as_tautology(monkeypatch):
    hints = [
        _claim("ModuleNotFoundError: No module named 'a'", "pytest --version"),
        _claim("ModuleNotFoundError: No module named 'b'", "mvn -version"),
        _claim("ModuleNotFoundError: No module named 'c'", "python --version"),
    ]
    real = _claim("ModuleNotFoundError: No module named 'd'", 'python -c "import d"')
    got = pick([*hints, real], k=8, ids=None, seen=set())
    assert [x.err for x in got] == [real.err]
    assert not hasattr(replay_mod, "_TAUTOLOGY")


def test_decide_skips_version_hint_without_replaying(tmp_path: Path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("replay must not run for a tautology eval")

    monkeypatch.setattr("claimidx.replay.replay", boom)
    scratch = tmp_path / "s"
    scratch.mkdir()
    c = _claim("ModuleNotFoundError: No module named 'a'", "pytest --version", fix_k="patch", fix_b="x")
    d = decide(c, scratch=scratch)
    assert d["action"] == "skip" and d["reason"] == "tautology-eval"


# 3. One _LOCAL_PIP regex, owned by policy.


def test_local_pip_regex_is_shared_from_policy():
    assert sandbox._LOCAL_PIP is policy._LOCAL_PIP
    assert replay_mod._LOCAL_PIP is policy._LOCAL_PIP
    assert not hasattr(replay_mod, "_PIP_EDITABLE")


# 4. Child output is decoded as UTF-8 regardless of the OS preferred encoding.

_UTF8_ERR = "import sys; sys.stderr.write('ValueError: caf\\u00e9 \\u2603\\n'); sys.exit(1)"


def test_run_command_decodes_utf8_child_output(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    rc, out = run_command([sys.executable, "-c", _UTF8_ERR], cwd=str(tmp_path), stream=False)
    assert rc == 1
    assert "ValueError: café ☃" in out
    assert _first_err_line(out) == "ValueError: café ☃"


def test_sandbox_replay_decodes_utf8_child_output(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    monkeypatch.setenv("CLAIMIDX_PYTHON", sys.executable)
    (tmp_path / "err.py").write_text(_UTF8_ERR + "\n", encoding="utf-8")
    r = replay("python err.py", 0, cwd=str(tmp_path))
    assert r.ran and r.rc == 1
    assert "ValueError: café ☃" in r.stderr


# 5. A child that exits 127 itself is the tree's failure; only the wrapper's spawn failure is not.


def test_after_run_child_exit_127_is_the_trees_failure(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    argv = [sys.executable, "-c", "import sys; sys.stderr.write('ModuleNotFoundError: No module named cix_zz\\n'); sys.exit(127)"]
    rc, out = run_command(argv, cwd=str(tmp_path), stream=False)
    assert rc == 127 and "ModuleNotFoundError" in out
    rec = after_run(store, argv, rc, out, cwd=str(tmp_path), emit=False)
    assert rec["rc"] == 127 and "verdict" in rec
    # The wrapper's own spawn failure still asks and remembers nothing.
    missing = ["no-such-command-cix-zz"]
    rc2, out2 = run_command(missing, cwd=str(tmp_path), stream=False)
    assert (rc2, out2) == (127, "")
    assert "verdict" not in after_run(store, missing, rc2, out2, cwd=str(tmp_path), emit=False)
