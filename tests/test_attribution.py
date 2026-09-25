"""Optional hangout/channel attribution on share events + funnel operator excludes."""

from __future__ import annotations

import json
from pathlib import Path

from claimidx.attribution import merge_attribution, resolve_attribution, sanitize_label
from claimidx.cli import main
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.home import share_claim
from claimidx.impact import commons_owner_proxy
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def test_sanitize_and_resolve(monkeypatch):
    assert sanitize_label(" Discord Hangout ") == "discord-hangout"
    assert sanitize_label("a/b") == "a-b"
    monkeypatch.delenv("CLAIMIDX_CHANNEL", raising=False)
    monkeypatch.delenv("CLAIMIDX_SOURCE", raising=False)
    assert resolve_attribution() == {}
    monkeypatch.setenv("CLAIMIDX_CHANNEL", "hn")
    monkeypatch.setenv("CLAIMIDX_SOURCE", "path-b")
    assert resolve_attribution() == {"channel": "hn", "source": "path-b"}
    assert resolve_attribution(channel="Discord", source=None) == {"channel": "discord", "source": "path-b"}
    assert merge_attribution({"exists": True}, {"channel": "hn"}) == {"exists": True, "channel": "hn"}


def _claim(own: str = "did:claimidx:stranger-x") -> Claim:
    err = "ModuleNotFoundError: No module named 'attrib_demo'"
    return Claim(
        fp=fingerprint(err=err, eco="py", rt="py@3.13"),
        cls="module_not_found",
        err=normalize_error(err),
        eco="py",
        rt="py@3.13",
        fix=Fix(k="pin", b="pip install attrib-demo"),
        eval=EvalSpec(cmd='python -c "import attrib_demo"'),
        own=own,
        src="local",
    )


def test_share_stamps_channel_on_commons_push_event(tmp_path: Path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(outbox))
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")  # force outbox/local projection path
    monkeypatch.setenv("CLAIMIDX_CHANNEL", "discord")
    monkeypatch.setenv("CLAIMIDX_SOURCE", "hangout")
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim())
    result = share_claim(store, c, channel=None, source=None)  # env wins
    assert result["status"] == "outbox"
    assert result.get("channel") == "discord"
    assert result.get("source") == "hangout"
    line = json.loads(outbox.read_text(encoding="utf-8").strip())
    assert line.get("channel") == "discord"
    assert line.get("source") == "hangout"
    events = [e for e in store.events(limit=20) if e.get("kind") == "home-propose"]
    assert events
    detail = events[0].get("detail") or {}
    assert detail.get("channel") == "discord"
    assert detail.get("source") == "hangout"


def test_cli_share_channel_flag(tmp_path: Path, monkeypatch, capsys):
    outbox = tmp_path / "outbox.jsonl"
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(outbox))
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    monkeypatch.delenv("CLAIMIDX_CHANNEL", raising=False)
    db = str(tmp_path / "ix.sqlite")
    store = Store(db)
    c = store.put(_claim("did:claimidx:social-bot"))
    assert main(["--db", db, "--fmt", "json", "share", c.id, "--channel", "hn", "--source", "path-b"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("channel") == "hn"
    assert out.get("source") == "path-b"


def test_commons_proxy_excludes_role_prefixes_and_operator_did(monkeypatch):
    from datetime import UTC, datetime

    now = datetime.now(UTC)

    def _c(own: str) -> Claim:
        err = f"RuntimeError: proxy {own}"
        return Claim(
            fp=fingerprint(err=err, eco="py"),
            cls="other",
            err=err,
            eco="py",
            fix=Fix(k="patch", b="x"),
            eval=EvalSpec(cmd="true"),
            own=own,
            nr=1,
            st="confirmed",
            ts=now,
        )

    rows = [
        _c("did:claimidx:stranger-a"),
        _c("did:claimidx:impl-falsifier-0711"),
        _c("did:claimidx:coo-grind"),
        _c("did:claimidx:social-outreach"),
        _c("did:claimidx:agent-5765cb"),  # auto-mint stays countable unless OPERATOR_DID
        _c("did:claimidx:grok"),
    ]
    out = commons_owner_proxy(days=30, claims=rows, ledger="test")
    assert "did:claimidx:stranger-a" in out["countable_ids"]
    assert "did:claimidx:agent-5765cb" in out["countable_ids"]
    assert "did:claimidx:impl-falsifier-0711" not in out["countable_ids"]
    assert "did:claimidx:coo-grind" not in out["countable_ids"]
    assert "did:claimidx:social-outreach" not in out["countable_ids"]
    assert "did:claimidx:grok" not in out["countable_ids"]
    assert out["countable"] == 2

    monkeypatch.setenv("CLAIMIDX_OPERATOR_DID", "did:claimidx:agent-5765cb,did:claimidx:stranger-*")
    out2 = commons_owner_proxy(days=30, claims=rows, ledger="test")
    assert "did:claimidx:agent-5765cb" not in out2["countable_ids"]
    assert out2["countable"] == 0  # stranger-a also matched stranger-*
