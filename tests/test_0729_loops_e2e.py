"""0.7.29: end-to-end loop close/reopen with real `claimidx run` subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from claimidx.home import parse_ledger
from claimidx.loopkey import loop_task, public_loop
from claimidx.mcp_server import handle
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


def _env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.pop("CLAIMIDX_SESSION", None)
    env.pop("CLAIMIDX_TASK", None)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    env["CLAIMIDX_CONFIG"] = str(tmp_path / "config.json")
    env["CLAIMIDX_OUTBOX"] = str(tmp_path / "outbox.jsonl")
    env["CLAIMIDX_LAST_FAILURE"] = str(tmp_path / "last_failure.json")
    env["CLAIMIDX_COMMONS"] = "0"
    env["CLAIMIDX_OWNER"] = "did:claimidx:test"
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return env


def _init_repo(repo: Path, body: str) -> None:
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "loop@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Loop Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "core.hooksPath", "/dev/null"], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "fail"], cwd=repo, check=True, capture_output=True)


def _run(tmp_path: Path, db: str, repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    argv = [
        sys.executable,
        "-m",
        "claimidx",
        "--db",
        db,
        "run",
        "--cwd",
        str(repo),
        "--",
        sys.executable,
        "app.py",
    ]
    return subprocess.run(argv, env=env, cwd=str(repo), capture_output=True, text=True)


def _mcp_loop(db: str) -> dict | None:
    called = handle(
        {"id": 1, "method": "tools/call", "params": {"name": "claimidx_session", "arguments": {}}},
        Store(db),
    )
    body = called["result"]
    content = body.get("structuredContent") or body
    return content.get("loop")


def _latest(db: str, repo: Path) -> dict | None:
    return Store(db).loop_latest(loop_task(str(repo)))


def test_run_subprocess_refuses_third_try_then_resets_closes_and_reopens(tmp_path: Path):
    """Fail x2, refuse the 3rd, reset on an artifact change, close on success, reopen without dropping the count."""
    repo = tmp_path / "repo"
    _init_repo(repo, "import sys\nsys.exit(1)\n")
    db = str(tmp_path / "ix.sqlite")
    env = _env(tmp_path)

    first = _run(tmp_path, db, repo, env)
    second = _run(tmp_path, db, repo, env)
    third = _run(tmp_path, db, repo, env)
    assert first.returncode == 1 and second.returncode == 1
    assert third.returncode == 75
    assert "ask/claim before retrying" in (third.stderr or "")
    after_refuse = _latest(db, repo)
    assert after_refuse and after_refuse["tries"] == 2 and after_refuse["state"] == "open" and after_refuse["stop"] is True
    mcp = _mcp_loop(db)
    assert public_loop(after_refuse) == mcp

    # Silent artifact change, same failure: the hard stop must not fire, and the counter resets.
    (repo / "app.py").write_text("import sys\n# changed\nsys.exit(1)\n", encoding="utf-8")
    reset = _run(tmp_path, db, repo, env)
    assert reset.returncode == 1
    assert "reset=artifact-changed" in (reset.stderr or "")
    after_reset = _latest(db, repo)
    assert after_reset and after_reset["key"] == after_refuse["key"]
    assert after_reset["tries"] == 1 and after_reset["state"] == "open" and after_reset["stop"] is False
    assert after_reset["reset_reason"] == "artifact-changed"
    assert public_loop(after_reset) == _mcp_loop(db)

    # Bump to two failures again, then succeed with a new artifact so the loop closes.
    again = _run(tmp_path, db, repo, env)
    assert again.returncode == 1
    (repo / "app.py").write_text("print('fixed')\n", encoding="utf-8")
    success = _run(tmp_path, db, repo, env)
    assert success.returncode == 0
    assert "CLAIMIDX loop closed" in (success.stderr or "")
    assert "reason=artifact-changed" in (success.stderr or "")
    closed = _latest(db, repo)
    assert closed and closed["state"] == "closed" and closed["tries"] == 2
    assert closed["stop"] is False
    assert closed.get("reset_reason") == "artifact-changed"
    assert public_loop(closed) == _mcp_loop(db)

    # The same failure reopens the same key without dropping the count.
    (repo / "app.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")
    reopen = _run(tmp_path, db, repo, env)
    assert reopen.returncode == 1
    opened = _latest(db, repo)
    assert opened and opened["key"] == closed["key"]
    assert opened["state"] == "open"
    assert opened["tries"] == 3
    assert opened["stop"] is True
    assert any(h.get("event") == "reopen" for h in opened["history"])
    assert public_loop(opened) == _mcp_loop(db)
