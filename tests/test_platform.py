import os
import sys
from pathlib import Path

from claimidx.home import pull
from claimidx.sandbox import replay, resolve_argv
from claimidx.store import Store


def test_python_eval_uses_this_interpreter():
    argv = resolve_argv(["python", "-c", "print(1)"])
    assert argv[0] == sys.executable
    argv3 = resolve_argv(["python3", "-c", "print(1)"])
    assert argv3[0] == sys.executable


def test_replay_python_print():
    result = replay('python -c "print(1)"', 0)
    assert result.held, result.as_dict()
    assert result.ran


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
    assert config_path().parts[-2:] == (".spoor", "config.json")
    assert DEFAULT_DB.parts[-2:] == (".spoor", "index.sqlite")
    if os.name == "nt":
        assert "\\" in str(DEFAULT_DB) or DEFAULT_DB.drive
    else:
        assert str(DEFAULT_DB).startswith("/") or str(DEFAULT_DB).startswith("~")
