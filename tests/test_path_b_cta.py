"""Path B mint CTA on ask / home-ask: force conversion toward countable commons DIDs."""

from __future__ import annotations

import json
from pathlib import Path

from claimidx.cli import main
from claimidx.impact import FIRST_HOLD_ID, FIRST_HOLD_RT, path_b_cta
from claimidx.query import ask
from claimidx.store import Store


def test_path_b_cta_uncountable_until_share(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-cta01"
    cta = path_b_cta(store, did)
    assert cta["minted"] is True
    assert cta["countable"] is False
    assert cta["shared"] is False
    assert cta["first_hold"] == {"id": FIRST_HOLD_ID, "rt": FIRST_HOLD_RT}
    assert FIRST_HOLD_ID in cta["next"]
    assert "claim --yes" in cta["next"]
    # One-shot: share is not a separate CTA step (recovery still uses claimidx share after publish_no_share).
    # Local publish alone is publish_no_share — still not countable.
    store.log("publish", did, "cix_localonly", {})
    cta_pub = path_b_cta(store, did)
    assert cta_pub["countable"] is False
    assert cta_pub["published"] is True
    assert cta_pub["next"] == "claimidx share"
    assert "publish_no_share" in cta_pub["why"]
    store.log("commons-push", did, "cix_deadbeef", {"exists": False})
    cta2 = path_b_cta(store, did)
    assert cta2["countable"] is True
    assert cta2["shared"] is True
    assert "next" not in cta2


def test_path_b_cta_staged_after_hold(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-cta02"
    store.log("confirm-replay", did, FIRST_HOLD_ID, {"held": True})
    cta = path_b_cta(store, did)
    assert cta["held"] is True
    assert cta["countable"] is False
    assert cta["next"] == "claimidx claim --yes"
    assert "&&" not in cta["next"]
    assert "hold alone" in cta["why"]


def test_ask_envelope_includes_path_b(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    out = ask("ModuleNotFoundError: No module named 'audioop'", eco="py", db=db)
    assert "path_b" in out
    assert out["path_b"]["minted"] is True
    assert out["path_b"]["countable"] is False
    assert out["path_b"]["first_hold"]["id"] == FIRST_HOLD_ID
    assert out["path_b"]["did"].startswith("did:claimidx:")
    assert out["path_b"]["did"] != "did:claimidx:anon"


def test_cli_ask_json_path_b(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "--fmt", "json", "ask", "--err", "ModuleNotFoundError: No module named 'x'", "--eco", "py"])
    captured = capsys.readouterr()
    assert rc in (0, 2), captured.err
    body = json.loads(captured.out)
    assert body["path_b"]["countable"] is False
    assert body["path_b"]["first_hold"]["id"] == FIRST_HOLD_ID
    assert body["path_b"]["minted"] is True


def test_home_ask_mints_and_returns_path_b(tmp_path: Path, monkeypatch):
    import claimidx.home as home
    from claimidx.home import ask_home

    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setattr(home, "fetch_ledger", lambda url=None: ([], [], "file://empty"))
    q = {"err": "RuntimeError: probe", "cls": "RuntimeError", "eco": "py", "rt": "", "dep": [], "fp": "x" * 64}
    out = ask_home(q, k=3, db=db)
    assert out["path_b"]["minted"] is True
    assert out["path_b"]["countable"] is False
    assert out["path_b"]["first_hold"]["id"] == FIRST_HOLD_ID
    store = Store(db)
    kinds = [e["kind"] for e in store.events(limit=20)]
    assert "ask" in kinds
