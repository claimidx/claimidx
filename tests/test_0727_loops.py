"""0.7.27 PR-B: persistent loop key, close/reopen, run output redaction."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from claimidx.cli import main
from claimidx.env import last_failure
from claimidx.home import parse_ledger
from claimidx.loopkey import UNKEYABLE, artifact_digest, loop_key, loop_task, scrub_line
from claimidx.store import Store

ROOT = Path(__file__).resolve().parents[1]


def test_full_commons_ledger_parses_with_zero_skips_and_unchanged_fps():
    """Pull of the shipped ledgers stays skipped==[] and fingerprints stay put."""
    rows = 0
    for name in ("claims.jsonl", "claims-claimidx.jsonl"):
        text = (ROOT / "data" / name).read_text(encoding="utf-8")
        raws = [json.loads(line) for line in text.splitlines() if line.strip()]
        claims, skipped = parse_ledger(text)
        assert skipped == [], skipped[:5]
        assert [c.fp for c in claims] == [r["fp"] for r in raws]
        rows += len(raws)
    assert rows >= 460


def test_loop_key_splits_different_commands_with_the_same_exit():
    k1 = loop_key("", "python -c 'raise SystemExit(1)'", 1, "")
    k2 = loop_key("", "false", 1, "")
    assert k1 != k2
    assert len(k1) == 64 and len(k2) == 64


def test_loop_key_stable_across_timestamps_and_temp_paths():
    a = "boom at 2026-11-21T15:00:00Z in /tmp/abc123/work pid=4412: failed"
    b = "boom at 2026-11-22T16:11:22Z in /tmp/xyz789/work pid=9981: failed"
    assert scrub_line(a) == scrub_line(b)
    assert loop_key(a, "make all", 1, a) == loop_key(b, "make all", 1, b)


def _fail_once(tmp_path: Path, db: str, extra: list[str] | None = None) -> int:
    return main(["--db", db, "run", "--cwd", str(tmp_path), *(extra or []), "--", sys.executable, "-c", "raise SystemExit(1)"])


def test_third_identical_run_is_refused_and_force_retry_overrides(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert _fail_once(tmp_path, db) == 1
    assert _fail_once(tmp_path, db) == 1
    rc = _fail_once(tmp_path, db)
    err = capsys.readouterr().err
    assert rc == 75
    assert "ask/claim before retrying" in err
    rc = _fail_once(tmp_path, db, extra=["--force-retry"])
    assert rc == 1


def test_loop_limit_zero_disables_the_hard_stop(tmp_path: Path, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setenv("CLAIMIDX_LOOP_LIMIT", "0")
    assert _fail_once(tmp_path, db) == 1
    assert _fail_once(tmp_path, db) == 1
    assert _fail_once(tmp_path, db) == 1


def test_timeout_is_recorded_as_unkeyable(tmp_path: Path, capsys):
    from claimidx.env import forget_failure

    db = str(tmp_path / "ix.sqlite")
    forget_failure()
    rc = main(
        [
            "--db",
            db,
            "run",
            "--cwd",
            str(tmp_path),
            "--timeout",
            "0.4",
            "--",
            sys.executable,
            "-c",
            "import time; print('starting', flush=True); time.sleep(8)",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 124
    assert "CLAIMIDX verdict" not in captured.err
    assert last_failure() is None
    assert "unkeyable" in captured.err and "timeout" in captured.err
    store = Store(db)
    entry = store.loop_get(UNKEYABLE, loop_task(str(tmp_path)))
    assert entry is not None
    assert entry["state"] == UNKEYABLE
    assert entry["tries"] >= 1
    assert entry["stop"] is False


def test_short_output_is_keyable(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert _fail_once(tmp_path, db) == 1
    err = capsys.readouterr().err
    assert "CLAIMIDX loop try 1/3" in err
    store = Store(db)
    latest = store.loop_latest(loop_task(str(tmp_path)))
    assert latest and latest["key"] != UNKEYABLE and latest["tries"] == 1


def test_cross_process_counting_without_session_env(tmp_path: Path, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    env = os.environ.copy()
    env.pop("CLAIMIDX_SESSION", None)
    env.pop("CLAIMIDX_TASK", None)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["CLAIMIDX_CONFIG"] = str(tmp_path / "config.json")
    env["CLAIMIDX_COMMONS"] = "0"
    argv = [
        sys.executable,
        "-m",
        "claimidx",
        "--db",
        db,
        "run",
        "--cwd",
        str(tmp_path),
        "--",
        sys.executable,
        "-c",
        "raise SystemExit(1)",
    ]
    first = subprocess.run(argv, env=env, cwd=str(tmp_path), capture_output=True, text=True)
    second = subprocess.run(argv, env=env, cwd=str(tmp_path), capture_output=True, text=True)
    third = subprocess.run(argv, env=env, cwd=str(tmp_path), capture_output=True, text=True)
    assert first.returncode == 1 and second.returncode == 1
    assert third.returncode == 75
    assert "ask/claim before retrying" in (third.stderr or "")


def test_mcp_session_loop_shape(tmp_path: Path):
    from claimidx.mcp_server import handle

    db = str(tmp_path / "ix.sqlite")
    store = Store(db)
    listed = handle({"id": 1, "method": "tools/list"}, store)
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    props = tools["claimidx_session"]["outputSchema"]["properties"]
    assert "loop" in props
    assert "must_ask" in props
    # Seed a failure so the session summary has a loop object.
    assert _fail_once(tmp_path, db) == 1
    called = handle({"id": 2, "method": "tools/call", "params": {"name": "claimidx_session", "arguments": {}}}, Store(db))
    body = called["result"]
    content = body.get("structuredContent") or {}
    loop = content.get("loop")
    if not loop:
        loop = Store(db).session_summary()["loop"]
    assert loop and "key" in loop and "tries" in loop and "stop" in loop
    assert loop["tries"] >= 1
    assert loop["stop"] is False


def test_unrelated_success_does_not_reset(tmp_path: Path):
    db = str(tmp_path / "ix.sqlite")
    assert _fail_once(tmp_path, db) == 1
    assert _fail_once(tmp_path, db) == 1
    store = Store(db)
    before = store.loop_latest(loop_task(str(tmp_path)))
    assert before and before["tries"] == 2 and before["state"] == "open"
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "-c", "print('ok')"])
    assert rc == 0
    after = Store(db).loop_get(before["key"], before["task"])
    assert after and after["tries"] == 2 and after["state"] == "open"


def test_fingerprint_change_resets_with_reason(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    task = loop_task(str(tmp_path))
    key = loop_key("err A", "cmd", 1, "err A")
    first = store.loop_bump(key, task=task, fp="fp-one", command="cmd", artifact="art1")
    second = store.loop_bump(key, task=task, fp="fp-two", command="cmd", artifact="art1")
    assert first["tries"] == 1
    assert second["tries"] == 1
    assert second["reset_reason"] == "fingerprint-changed"
    assert second["event"] == "reset"


def test_artifact_change_resets_with_reason(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    task = loop_task(str(tmp_path))
    key = loop_key("err B", "cmd", 1, "err B")
    store.loop_bump(key, task=task, fp="fp", command="cmd", artifact="digest-a")
    store.loop_bump(key, task=task, fp="fp", command="cmd", artifact="digest-a")
    reset = store.loop_bump(key, task=task, fp="fp", command="cmd", artifact="digest-b")
    assert reset["tries"] == 1
    assert reset["reset_reason"] == "artifact-changed"


def test_close_reopen_keeps_the_count(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    task = loop_task(str(tmp_path))
    key = loop_key("err C", "cmd", 1, "err C")
    store.loop_bump(key, task=task, fp="fp", command="cmd", artifact="a1")
    store.loop_bump(key, task=task, fp="fp", command="cmd", artifact="a1")
    closed = store.loop_close(key, task=task, artifact="a2", reason="artifact-changed")
    assert closed and closed["state"] == "closed" and closed["tries"] == 2
    reopened = store.loop_bump(key, task=task, fp="fp", command="cmd", artifact="a2")
    assert reopened["state"] == "open"
    assert reopened["tries"] == 3
    assert reopened["event"] == "reopen"
    assert any(h.get("event") == "close" for h in reopened["history"])


def test_success_with_changed_artifact_closes_the_loop(tmp_path: Path):
    db = str(tmp_path / "ix.sqlite")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "loop@test"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Loop Test"], cwd=tmp_path, check=True, capture_output=True)
    app = tmp_path / "app.py"
    app.write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "fail"], cwd=tmp_path, check=True, capture_output=True)
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    assert rc == 1
    before = artifact_digest(f"{sys.executable} app.py", str(tmp_path))
    app.write_text("print('fixed')\n", encoding="utf-8")
    after = artifact_digest(f"{sys.executable} app.py", str(tmp_path))
    assert before and after and before != after
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    assert rc == 0
    store = Store(db)
    latest = store.loop_latest(loop_task(str(tmp_path)))
    assert latest and latest["state"] == "closed"
    assert latest.get("reset_reason") == "artifact-changed"


def test_run_redacts_terminal_output_by_default(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    app = tmp_path / "app.py"
    app.write_text("print('redis://user:hunter2@localhost:6379')\n", encoding="utf-8")
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "hunter2" not in captured.out
    assert "<CRED>" in captured.out
    rec = last_failure()
    if rec:
        assert "hunter2" not in json.dumps(rec)


def test_run_raw_flag_and_env_leave_output_unredacted(tmp_path: Path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    app = tmp_path / "app.py"
    app.write_text("print('redis://user:hunter2@localhost:6379')\n", encoding="utf-8")
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--raw", "--", sys.executable, "app.py"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "hunter2" in captured.out
    monkeypatch.setenv("CLAIMIDX_RUN_RAW", "1")
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "hunter2" in captured.out


def test_hook_sensor_sets_stop_after_two_failures(tmp_path: Path):
    from claimidx.hook import sensor

    store = Store(tmp_path / "ix.sqlite")
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_input": {"command": "python -c 'raise SystemExit(1)'"},
            "cwd": str(tmp_path),
            "exit_code": 1,
            "stderr": "RuntimeError: boom from hook loop test",
        }
    )
    first = sensor(store, payload)
    second = sensor(store, payload)
    assert first.get("stop") is not True
    assert second.get("stop") is True
    assert second.get("loop") and second["loop"]["tries"] == 2
    assert second["loop"]["stop"] is True
