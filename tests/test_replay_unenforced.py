"""0.7.16: default replay is offline only where it is enforced (bwrap).

Where it is not (macOS, Windows, a Linux host without bwrap), a default replay
that the sandbox cannot keep offline refuses loudly, exits nonzero, and records
nothing. `--net` allows the network explicitly and is recorded with net=open on
the confirm detail; it never mints nr. A cancelled tool run is not a pass.
Source: Validated Results/offline-replay-unenforced-no-bwrap-20261020.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from claimidx import sandbox
from claimidx.cli import main
from claimidx.store import Store

# `python -I` turns off the interpreter hook (PYTHONPATH is ignored), so without bwrap net=unenforced.
_UNENFORCED_EVAL = 'python -I -c "import json"'


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


@pytest.fixture
def no_bwrap(monkeypatch):
    real = shutil.which

    def which(name, *a, **k):
        return None if name == "bwrap" else real(name, *a, **k)

    monkeypatch.setattr(sandbox.shutil, "which", which)


def _publish(db: str, capsys, *, err: str, eval_cmd: str = _UNENFORCED_EVAL) -> str:
    argv = ["--db", db, "--fmt", "id", "publish", "--err", err, "--eco", "py", "--rt", _py_rt()]
    argv += ["--fix-k", "constraint", "--fix-b", "ok", "--eval", eval_cmd]
    assert main(argv) == 0
    return capsys.readouterr().out.strip()


def _confirm(db: str, capsys, cid: str, *extra: str) -> tuple[int, dict, str]:
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", *extra, cid])
    cap = capsys.readouterr()
    return rc, json.loads(cap.out), cap.err


def _kinds(db: str, cid: str) -> list[str]:
    return [e.get("kind") or e.get("ev") or "" for e in Store(db).events(limit=50) if e.get("claim_id", e.get("id")) == cid]


def test_gate_refuses_unenforced_default_replay_even_without_a_fetch():
    from claimidx.gate import UNENFORCED_PREFIX, graduation_gate
    from claimidx.models import Claim, EvalSpec, Fix

    from claimidx.fingerprint import fingerprint

    err = "TypeError: python -I connected out silently"
    claim = Claim(
        fp=fingerprint(err=err, eco="py", rt=_py_rt()),
        cls="type_error",
        err=err,
        eco="py",
        rt=_py_rt(),
        fix=Fix(k="constraint", b="ok"),
        eval=EvalSpec(cmd="python -I check.py"),
    )
    held = sandbox.ReplayResult(True, True, 0, 0, True, "held", stdout="connected ('1.1.1.1', 80)\n", env=_py_rt(), net="unenforced", net_how="python-flags")
    decision = graduation_gate(claim, held)
    assert decision.mint_nr is False and decision.record_net is False
    assert decision.reason.startswith(UNENFORCED_PREFIX)
    refusal = decision.refusal()
    assert refusal["error"] == UNENFORCED_PREFIX
    assert "bwrap" in refusal["message"] and "--net" in refusal["message"]


def test_cli_confirm_unenforced_without_net_exits_nonzero_and_mints_no_nr(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: unenforced default replay must not mint")
    rc, out, err = _confirm(db, capsys, cid)
    assert rc != 0
    assert out["recorded"] is False
    assert out["replay"]["net"] == "unenforced" and out["replay"]["net_how"] == "python-flags"
    assert out["reason"].startswith("replay-net-unenforced")
    assert "OFFLINE REPLAY IS NOT ENFORCED" in err and "bwrap" in err and "--net" in err
    c = Store(db).get(cid)
    assert c.nr == 0 and c.nc == 0 and c.nf == 0
    assert "confirm-replay" not in _kinds(db, cid) and "confirm" not in _kinds(db, cid)


def test_cli_confirm_net_is_allowed_and_recorded_with_net_open(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: an explicit net run is recorded")
    rc, out, _err = _confirm(db, capsys, cid, "--net")
    assert rc == 0, out
    assert out["recorded"] is True and out["nr_minted"] is False
    assert out["replay"]["net"] == "open" and out["net"] == "open"
    assert out["reason"].startswith("net-open")
    c = Store(db).get(cid)
    assert c.nr == 0 and c.nc == 1
    conf = [e for e in Store(db).events(limit=50) if (e.get("kind") or e.get("ev")) == "confirm"]
    assert conf, Store(db).events(limit=50)
    blob = json.dumps(conf[0], default=str)
    assert '"net": "open"' in blob and '"nr_minted": false' in blob


def test_mcp_confirm_unenforced_is_error_and_records_nothing(tmp_path: Path, capsys, no_bwrap):
    from claimidx.mcp_server import handle

    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: mcp unenforced replay")
    store = Store(db)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claimidx_confirm", "arguments": {"id": cid, "replay": True}}}
    resp = handle(msg, store)
    result = resp["result"]
    assert result.get("isError") is True
    body = result["structuredContent"]
    assert body["recorded"] is False and body["reason"].startswith("replay-net-unenforced")
    assert "bwrap" in body["message"]
    c = store.get(cid)
    assert c.nr == 0 and c.nc == 0


def test_mcp_confirm_net_is_recorded_without_nr(tmp_path: Path, capsys, no_bwrap):
    from claimidx.mcp_server import _call

    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: mcp net replay")
    store = Store(db)
    out = _call("claimidx_confirm", {"id": cid, "replay": True, "net": True}, store)
    assert out["recorded"] is True and out["nr_minted"] is False and out["net"] == "open"
    c = store.get(cid)
    assert c.nr == 0 and c.nc == 1


def test_batch_verify_unenforced_exits_nonzero_and_mints_no_nr(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: batch replay unenforced")
    rc = main(["--db", db, "--fmt", "json", "verify", "--apply", "--id", cid])
    cap = capsys.readouterr()
    assert rc != 0, cap.out
    report = json.loads(cap.out)
    row = next(r for r in report["results"] if r["id"] == cid)
    assert row["action"] == "skip" and row["reason"].startswith("replay-net-unenforced")
    assert "OFFLINE REPLAY IS NOT ENFORCED" in cap.err
    c = Store(db).get(cid)
    assert c.nr == 0 and c.nc == 0


def test_interpreter_offline_without_bwrap_still_mints(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: interpreter hook path still mints", eval_cmd='python -c "import json"')
    rc, out, _err = _confirm(db, capsys, cid)
    assert rc == 0, out
    assert out["replay"]["net"] == "offline" and out["replay"]["net_how"] == "interpreter"
    assert Store(db).get(cid).nr == 1


def test_bwrap_argv_keeps_cwd_and_dev_null_usable(tmp_path: Path):
    argv = sandbox._bwrap_argv(["python", "-c", "1"], str(tmp_path))
    assert argv.index("--tmpfs") < argv.index("--bind"), "tmpfs /tmp must not hide a --cwd under /tmp"
    assert argv[argv.index("--dev") + 1] == "/dev"
    assert "--unshare-net" in argv


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("bwrap"), reason="needs bwrap")
def test_bwrap_enforced_replay_still_mints_and_blocks_network(tmp_path: Path, capsys):
    tree = tmp_path / "proj"
    tree.mkdir()
    (tree / "check.py").write_text("import json\nprint('ok')\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: bwrap path still mints", eval_cmd="python -I check.py")
    rc, out, _err = _confirm(db, capsys, cid, "--cwd", str(tree))
    assert rc == 0, (out.get("reason"), out.get("replay"))
    assert out["replay"]["net"] == "offline" and out["replay"]["net_how"] == "bwrap"
    assert Store(db).get(cid).nr == 1
    probe = sandbox.replay("python -I -c \"import socket; socket.create_connection(('1.1.1.1', 80), timeout=3)\"", 0, cwd=str(tree))
    assert probe.ran and probe.net_how == "bwrap" and probe.held is False
    assert "unreachable" in probe.stderr.lower() or "errno" in probe.stderr.lower()


def test_hook_cancelled_after_a_failure_is_not_a_pass(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    fail = json.dumps({"hook_event_name": "postToolUseFailure", "command": "sleep 999", "error_message": "timed out", "failure_type": "timeout"})
    assert main(["--db", db, "hook", "--err", fail]) == 0
    capsys.readouterr()
    cancelled = json.dumps({"hook_event_name": "PostToolUse", "command": "sleep 999", "output": "", "failure_type": "cancelled"})
    assert main(["--db", db, "hook", "--err", cancelled]) == 0
    out = capsys.readouterr().out
    assert "CLAIMIDX fixed" not in out and "now passes" not in out
    # A real pass of the same command still nudges.
    ok = json.dumps({"hook_event_name": "PostToolUse", "command": "sleep 999", "output": "", "exit_code": 0})
    assert main(["--db", db, "hook", "--err", ok]) == 0
    assert "CLAIMIDX fixed" in capsys.readouterr().out


# --- claim path: the claim is published (exit 0); only the replay is refused, and it says so plainly ---

_OLD_CONTRADICTION = "nr minted from the working tree only"
_BROKEN = "import json\n\ndef load(t):\n    return json.loads(t, encoding='utf-8')\n"
_FIXED = "import json\n\ndef load(t):\n    return json.loads(t)\n"
_LOADS_ERR = "TypeError: loads() got an unexpected keyword argument 'encoding'"


def _git_tree(tmp_path: Path) -> Path:
    import subprocess

    tree = tmp_path / "repo"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / "mod.py").write_text(_BROKEN, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "broken"], cwd=tree, check=True)
    (tree / "mod.py").write_text(_FIXED, encoding="utf-8")  # the fix, uncommitted
    return tree


def _claim_cli(db: str, capsys, *args: str) -> tuple[int, dict, str]:
    rc = main(["--db", db, "--fmt", "json", "claim", *args, "--yes"])
    cap = capsys.readouterr()
    return rc, json.loads(cap.out), cap.err


def _assert_claim_replay_refused(db: str, rc: int, out: dict, err: str) -> None:
    assert rc == 0, out  # the claim itself succeeded; agents must not read Path B as failed
    assert out["ok"] is True and out["id"]
    assert out["replay"]["recorded"] is False
    assert "replay-net-unenforced" in str(out["replay"]["reason"])
    warns = " | ".join(out.get("warn") or [])
    assert "replay skipped" in warns and "not enforced" in warns and "no nr was minted" in warns
    assert "bwrap" in warns and "--net" in warns
    assert _OLD_CONTRADICTION not in json.dumps(out) and _OLD_CONTRADICTION not in err
    assert "OFFLINE REPLAY IS NOT ENFORCED" in err
    c = Store(db).get(out["id"])
    assert c.nr == 0 and c.nc == 0 and c.nf == 0
    kinds = [e["kind"] for e in Store(db).events(limit=50)]
    assert "confirm-replay" not in kinds and "confirm" not in kinds and "fail" not in kinds
    if "nr" in out:
        assert out["nr"] == 0


def test_cli_claim_unenforced_exits_0_mints_nothing_and_says_replay_skipped(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    rc, out, err = _claim_cli(
        db,
        capsys,
        "--err",
        "ModuleNotFoundError: No module named 'json'",
        "--cwd",
        str(tree),
        "--eval",
        _UNENFORCED_EVAL,
        "--no-diff",
        "--fix",
        "pip install json",
    )
    _assert_claim_replay_refused(db, rc, out, err)


def test_clean_room_claim_unenforced_exits_0_mints_nothing_and_says_replay_skipped(tmp_path: Path, capsys, no_bwrap):
    tree = _git_tree(tmp_path)
    db = str(tmp_path / "ix.sqlite")
    rc, out, err = _claim_cli(db, capsys, "--err", _LOADS_ERR, "--cwd", str(tree), "--eval", "python -S -c \"import mod; mod.load('{}')\"")
    assert out["clean_room"]["ran"] is True, out["clean_room"]
    _assert_claim_replay_refused(db, rc, out, err)


def test_clean_room_skipped_claim_unenforced_never_says_nr_minted(tmp_path: Path, capsys, no_bwrap):
    tree = _git_tree(tmp_path)
    db = str(tmp_path / "ix.sqlite")
    rc, out, err = _claim_cli(
        db,
        capsys,
        "--err",
        _LOADS_ERR,
        "--cwd",
        str(tree),
        "--eval",
        "python -S -c \"import mod; mod.load('{}')\"",
        "--fix",
        "removed the encoding kwarg",
        "--fix-k",
        "patch",
    )
    assert out["clean_room"]["ran"] is False
    _assert_claim_replay_refused(db, rc, out, err)


def test_onboarding_without_bwrap_claim_and_apply_still_hold(tmp_path: Path, capsys, no_bwrap):
    """Hook-covered `python -c` evals still mint nr=1 and exit 0 on hosts without bwrap."""
    import subprocess

    tree = _git_tree(tmp_path)
    db = str(tmp_path / "ix.sqlite")
    ev = "python -c \"import mod; mod.load('{}')\""
    rc, out, err = _claim_cli(db, capsys, "--err", _LOADS_ERR, "--cwd", str(tree), "--eval", ev, "--no-replay")
    assert rc == 0
    cid = out["id"]
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tree), str(other)], check=True)
    assert main(["--db", db, "--fmt", "json", "apply", cid, "--cwd", str(other), "--yes"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["applied"] is True and applied["replay"]["recorded"] is True and applied["replay"]["nr"] == 1
    assert applied["replay"]["replay"]["net_how"] == "interpreter"


def test_claim_share_yes_without_bwrap_still_reaches_commons(tmp_path: Path, capsys, no_bwrap, monkeypatch):
    from claimidx import home
    from claimidx.env import remember_failure

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: calls.append(url) or {"exists": False, "claim": {"id": payload.get("id")}})
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--share-yes", "--no-diff", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out.get("share", {}).get("status") == "commons"
    assert any(u.endswith("/api/publish") for u in calls)
    assert out["replay"]["recorded"] is True and Store(db).get(out["id"]).nr == 1
