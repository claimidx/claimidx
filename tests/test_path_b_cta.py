"""Path B status on ask / home-ask / claim: next only names the user's own claim/share step.

0.7.17: no hold codes, no `apply --yes`, no "invite another agent" in path_b (audit M11).
"""

from __future__ import annotations

import json
from pathlib import Path

from claimidx.cli import main
from claimidx.impact import INSTALL_CMD, INSTALL_MIN, SHARE_CMD, path_b_cta
from claimidx.query import ask
from claimidx.store import Store

HELD_ID = "cix_0000000000000001"


def _assert_clean(cta: dict) -> None:
    text = json.dumps(cta)
    assert "cix_bdc" not in text
    assert "first_hold" not in cta
    assert "apply" not in cta["next"]
    assert "invite" not in text
    assert "stranger" not in text
    assert "--channel" not in text and "--source" not in text


def test_path_b_cta_uncountable_until_share(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-cta01"
    cta = path_b_cta(store, did)
    assert cta["minted"] is True
    assert cta["countable"] is False
    assert cta["shared"] is False
    assert cta["next"] == SHARE_CMD == "claimidx claim --yes --share-yes"
    _assert_clean(cta)
    # Local publish alone is publish_no_share — still not countable.
    store.log("publish", did, "cix_localonly", {})
    cta_pub = path_b_cta(store, did)
    assert cta_pub["countable"] is False
    assert cta_pub["published"] is True
    assert cta_pub["next"] == "claimidx share --yes"
    assert "publish_no_share" in cta_pub["why"]
    _assert_clean(cta_pub)
    store.log("commons-push", did, "cix_deadbeef", {"exists": False})
    cta2 = path_b_cta(store, did)
    assert cta2["countable"] is True
    assert cta2["shared"] is True
    assert SHARE_CMD in cta2["next"]
    _assert_clean(cta2)


def test_path_b_cta_staged_after_hold(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-cta02"
    store.log("confirm-replay", did, HELD_ID, {"held": True})
    cta = path_b_cta(store, did)
    assert cta["held"] is True
    assert cta["countable"] is False
    assert cta["next"] == "claimidx claim --yes --share-yes"
    assert "&&" not in cta["next"]
    assert "hold alone" in cta["why"]
    _assert_clean(cta)


def test_ask_envelope_includes_path_b(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    out = ask("ModuleNotFoundError: No module named 'audioop'", eco="py", db=db)
    assert "path_b" in out
    assert out["path_b"]["minted"] is True
    assert out["path_b"]["countable"] is False
    assert out["path_b"]["next"] == SHARE_CMD
    assert out["path_b"]["did"].startswith("did:claimidx:")
    assert out["path_b"]["did"] != "did:claimidx:anon"
    _assert_clean(out["path_b"])


def test_cli_ask_json_path_b(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "--fmt", "json", "ask", "--err", "ModuleNotFoundError: No module named 'x'", "--eco", "py"])
    captured = capsys.readouterr()
    assert rc in (0, 2), captured.err
    body = json.loads(captured.out)
    assert body["path_b"]["countable"] is False
    assert body["path_b"]["minted"] is True
    _assert_clean(body["path_b"])


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
    _assert_clean(out["path_b"])
    store = Store(db)
    kinds = [e["kind"] for e in store.events(limit=20)]
    assert "ask" in kinds


def test_path_b_cta_after_countable_is_not_an_invite(tmp_path: Path):
    """After a countable share, next is the user's own next claim — never a growth invite."""
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-countable01"
    store.log("commons-push", did, "cix_deadbeef", {"exists": False})
    cta = path_b_cta(store, did)
    assert cta["countable"] is True
    _assert_clean(cta)


def test_install_pin_is_this_release():
    from claimidx import __version__

    assert INSTALL_MIN == __version__
    assert INSTALL_CMD == f'pip install -U "claimidx[server]>={__version__}"'
