import os
import sys
from pathlib import Path

from claimidx.home import pull
from claimidx.sandbox import replay, resolve_argv
from claimidx.store import Store


def test_python_eval_follows_path_then_this_interpreter(monkeypatch, tmp_path):
    fake = tmp_path / "python.exe"
    fake.write_text("", encoding="utf-8")
    monkeypatch.delenv("CLAIMIDX_PYTHON", raising=False)
    monkeypatch.setattr("claimidx.sandbox._which", lambda head: str(fake) if head in ("python", "python3") else None)
    argv = resolve_argv(["python", "-c", "print(1)"])
    assert argv[0] == str(fake)
    monkeypatch.setenv("CLAIMIDX_PYTHON", str(sys.executable))
    pinned = resolve_argv(["python", "-c", "print(1)"])
    assert pinned[0] == sys.executable


def test_replay_python_print():
    result = replay('python -c "print(1)"', 0)
    assert result.held, result.as_dict()
    assert result.ran
    assert result.env == f"py@{sys.version_info.major}.{sys.version_info.minor}"


def test_replay_true_builtin_everywhere():
    assert replay("true", 0).held
    assert not replay("false", 0).held


def test_file_uri_ledger_roundtrip(tmp_path: Path):
    from claimidx.fingerprint import fingerprint, normalize_error
    from claimidx.home import propose_line
    from claimidx.models import Claim, EvalSpec, Fix

    err = "TypeError: params is a Promise"
    c = Claim(
        fp=fingerprint(err=err, eco="npm", rt="node@20", dep=["next@15.0.0"]),
        cls="async_api",
        err=normalize_error(err),
        eco="npm",
        rt="node@20",
        dep=["next@15.0.0"],
        fix=Fix(k="patch", b="const { slug } = await params"),
        eval=EvalSpec(cmd="npx tsc --noEmit"),
        own="did:claimidx:harper",
    )
    ledger = tmp_path / "home.jsonl"
    ledger.write_text(propose_line(c) + "\n", encoding="utf-8")
    store = Store(tmp_path / "ix.sqlite")
    result = pull(store, url=ledger.as_uri())
    assert result["imported"] == 1
    assert store.get(c.id) is not None


def test_config_and_db_live_under_home(tmp_path, monkeypatch):
    from claimidx.config import config_path
    from claimidx.store import DEFAULT_DB

    monkeypatch.delenv("CLAIMIDX_CONFIG", raising=False)
    path = config_path()
    assert path.name == "config.json"
    assert path.parts[-2] == ".claimidx"
    assert DEFAULT_DB.name == "index.sqlite"
    assert DEFAULT_DB.parts[-2] == ".claimidx"
    if os.name == "nt":
        assert "\\" in str(DEFAULT_DB) or DEFAULT_DB.drive
    else:
        assert str(DEFAULT_DB).startswith("/") or str(DEFAULT_DB).startswith("~")
