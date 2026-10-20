"""Path B: claim --yes is local; commons needs --share-yes / share_yes (CLI + MCP)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from claimidx import home
from claimidx.cli import main
from claimidx.env import remember_failure
from claimidx.impact import FIRST_HOLD_ID, path_b_cta
from claimidx.mcp_server import _call
from claimidx.store import Store


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


@pytest.fixture
def commons(monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    calls: list[tuple[str, dict]] = []

    def fake_post(url, payload, token="", timeout=20.0):
        calls.append((url, payload))
        return {"exists": False, "claim": {"id": payload.get("id")}}

    monkeypatch.setattr(home, "_post", fake_post)
    return calls


def test_path_b_cta_after_hold_requires_share_yes(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-oneshot"
    store.log("confirm-replay", did, FIRST_HOLD_ID, {"held": True})
    cta = path_b_cta(store, did)
    assert cta["held"] is True
    assert cta["countable"] is False
    assert cta["next"] == "claimidx claim --yes --share-yes"
    assert "&&" not in cta["next"]
    assert "share-yes" in cta["why"] or "confirm then commons" in cta["why"]


def test_claim_without_failure_still_loud(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "claim", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "pass --err" in err or "no failure" in err


def test_claim_yes_alone_stays_local_not_countable(tmp_path: Path, commons, capsys, monkeypatch):
    """Decline / no confirm: claim --yes must NOT silently mean commons."""
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["id"]
    share = out.get("share") or {}
    assert share.get("status") in {"needs_confirm", "local"} or (share.get("commons") or {}).get("status") == "needs_confirm"
    assert not any(u.endswith("/api/publish") for u, _ in commons)
    store = Store(db)
    assert not home.commons_shared(store, out["id"])
    kinds = {e["kind"] for e in store.events(limit=50)}
    assert "commons-push" not in kinds
    assert "publish" in kinds
    assert out.get("path_b", {}).get("countable") is False


def test_claim_yes_share_yes_reaches_commons(tmp_path: Path, commons, capsys, monkeypatch):
    """Countable path: explicit --share-yes after review."""
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--share-yes", "--no-diff", "--fix", "pip install json"]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] and out.get("share", {}).get("status") == "commons"
    assert "commons review" in captured.err
    assert any(u.endswith("/api/publish") for u, _ in commons)
    store = Store(db)
    assert home.commons_shared(store, out["id"])
    assert "commons-push" in {e["kind"] for e in store.events(limit=50)}


def test_claim_yes_share_alias_online(tmp_path: Path, commons, capsys, monkeypatch):
    """`--share` remains an alias of `--share-yes`."""
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--share", "--no-diff", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out.get("share", {}).get("status") == "commons"


def test_claim_yes_local_does_not_force_share(tmp_path: Path, commons, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--local", "--no-diff", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["id"]
    assert out.get("share", {}).get("status") == "local"
    assert not commons
    store = Store(db)
    assert home.keep_local(store, out["id"])
    assert not home.commons_shared(store, out["id"])


def test_mcp_claim_yes_alone_stays_local(tmp_path: Path, commons, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    store = Store(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    out = _call(
        "claimidx_claim",
        {
            "err": "ModuleNotFoundError: No module named 'json'",
            "cwd": str(tree),
            "no_diff": True,
            "rt": _py_rt(),
            "fix": "pip install json",
            "yes": True,
        },
        store,
    )
    assert out["ok"] and out["id"]
    share = out.get("share") or {}
    assert share.get("status") in {"needs_confirm", "local"} or (share.get("commons") or {}).get("status") == "needs_confirm"
    assert not home.commons_shared(store, out["id"])
    assert not commons
    assert out.get("path_b", {}).get("countable") is False


def test_mcp_claim_share_yes_reaches_commons(tmp_path: Path, commons, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    store = Store(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    out = _call(
        "claimidx_claim",
        {
            "err": "ModuleNotFoundError: No module named 'json'",
            "cwd": str(tree),
            "no_diff": True,
            "rt": _py_rt(),
            "fix": "pip install json",
            "yes": True,
            "share_yes": True,
        },
        store,
    )
    assert out["ok"] and out.get("share", {}).get("status") == "commons"
    assert home.commons_shared(store, out["id"])
    assert "review" in out or "review" in (out.get("share") or {})
    assert "path_b" in out


def test_mcp_claim_local_does_not_force_share(tmp_path: Path, commons, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    store = Store(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    out = _call(
        "claimidx_claim",
        {
            "err": "ModuleNotFoundError: No module named 'json'",
            "cwd": str(tree),
            "no_diff": True,
            "rt": _py_rt(),
            "fix": "pip install json",
            "yes": True,
            "local": True,
        },
        store,
    )
    assert out["ok"] and out.get("share", {}).get("status") == "local"
    assert not commons
    assert home.keep_local(store, out["id"])


def test_share_decline_stays_local(tmp_path: Path, commons, monkeypatch):
    """claimidx_share without share_yes returns needs_confirm; not countable."""
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    store = Store(tmp_path / "ix.sqlite")
    from claimidx.query import ingest

    row = ingest(
        "ModuleNotFoundError: No module named 'json'",
        fix_k="pin",
        fix_b="json==1",
        eval='python -c "import json"',
        eco="py",
        own="did:claimidx:agent-decline",
        db=store.path,
    )
    cid = row["id"]
    out = _call("claimidx_share", {"id": cid}, store)
    assert out["status"] == "needs_confirm"
    assert out.get("review", {}).get("destination") == "commons"
    assert not commons
    assert not home.commons_shared(store, cid)
    yes = _call("claimidx_share", {"id": cid, "share_yes": True}, store)
    assert yes.get("status") == "commons" or (yes.get("commons") or {}).get("status") == "commons"
    assert home.commons_shared(store, cid)
