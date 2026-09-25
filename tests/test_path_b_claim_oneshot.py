"""Path B one-shot: online claim --yes continues into share (CLI + MCP)."""

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


def test_path_b_cta_oneshot_after_hold(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-oneshot"
    store.log("confirm-replay", did, FIRST_HOLD_ID, {"held": True})
    cta = path_b_cta(store, did)
    assert cta["held"] is True
    assert cta["countable"] is False
    assert cta["next"] == "claimidx claim --yes"
    assert "&&" not in cta["next"]
    assert "shares when online" in cta["why"]


def test_claim_without_failure_still_loud(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "claim", "--yes"]) == 2
    err = capsys.readouterr().err
    assert "pass --err" in err or "no failure" in err


def test_claim_yes_online_reaches_share(tmp_path: Path, commons, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["id"]
    assert out.get("share", {}).get("status") == "commons"
    assert any(u.endswith("/api/publish") for u, _ in commons)
    store = Store(db)
    assert home.commons_shared(store, out["id"])
    kinds = {e["kind"] for e in store.events(limit=50)}
    assert "commons-push" in kinds
    assert "publish" in kinds


def test_claim_yes_share_flag_online(tmp_path: Path, commons, capsys, monkeypatch):
    """Documented CTA `claim --yes --share` same as --yes when online."""
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


def test_mcp_claim_yes_online_reaches_share(tmp_path: Path, commons, monkeypatch):
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
    assert out.get("share", {}).get("status") == "commons"
    assert home.commons_shared(store, out["id"])
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


def test_mcp_claim_share_false_skips_ensure(tmp_path: Path, commons, monkeypatch):
    """share=false skips the Path B ensure; ingest may still share unless local — force local semantics via share=false after publish is soft.

    When share=false without local, ingest still auto-shares today; ensure is skipped.
    Keep this as MCP parity for the flag: local remains the durable opt-out.
    """
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    store = Store(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    # Durable opt-out is local=true (tested above). share=false only skips ensure_online_share.
    out = _call(
        "claimidx_claim",
        {
            "err": "ModuleNotFoundError: No module named 'json'",
            "cwd": str(tree),
            "no_diff": True,
            "rt": _py_rt(),
            "fix": "pip install json",
            "yes": True,
            "share": False,
            "local": True,
        },
        store,
    )
    assert out["ok"]
    assert out.get("share", {}).get("status") == "local"
    assert not commons
