from pathlib import Path

from claimidx.cli import main
from claimidx.store import Store
from claimidx.team import activity, resolve_owner, whoami


def test_resolve_owner_env(monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:agent-a")
    assert resolve_owner() == "did:claimidx:agent-a"
    assert whoami()["wired"] is True
    assert whoami()["agent"] == "agent-a"


def test_resolve_owner_agent_name(monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.setenv("CLAIMIDX_AGENT", "agent-b")
    assert resolve_owner() == "did:claimidx:agent-b"


def test_any_agent_any_provider_is_wired(monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.setenv("CLAIMIDX_AGENT", "Codex CLI")
    assert resolve_owner() == "did:claimidx:codex-cli"
    me = whoami()
    assert me["wired"] is True
    assert me["listed"] is False
    assert me["agent"] == "codex-cli"


def test_foreign_did_method_is_wired(monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:web:example.com:alice")
    me = whoami()
    assert me["did"] == "did:web:example.com:alice"
    assert me["wired"] is True
    assert me["listed"] is False


def test_whoami_team_ingest(tmp_path: Path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:agent-c")
    assert main(["--db", db, "whoami"]) == 0
    assert "did:claimidx:agent-c" in capsys.readouterr().out
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "id",
            "ingest",
            "--err",
            "ModuleNotFoundError: No module named 'wired_mod'",
            "--eco",
            "py",
            "--fix-k",
            "pin",
            "--fix-b",
            "pip install wired-mod",
            "--eval",
            "true",
        ]
    )
    assert rc == 0
    cid = capsys.readouterr().out.strip()
    assert cid.startswith(("spr_", "cix_"))
    assert main(["--db", db, "team"]) == 0
    out = capsys.readouterr().out
    assert "did:claimidx:agent-c" in out
    assert "publish" in out


def test_activity_keeps_unlisted_providers_when_operator_floods(tmp_path: Path):
    """events(limit=500) DESC drops older callers. The home is every DID, not grok."""
    store = Store(tmp_path / "ix.sqlite")
    store.log("ask", "did:claimidx:claude", "")
    store.log("ask", "did:claimidx:codex", "")
    for _ in range(500):
        store.log("ask", "did:claimidx:agent-d", "")
    rows = activity(store)
    dids = {r["did"] for r in rows}
    assert "did:claimidx:claude" in dids
    assert "did:claimidx:codex" in dids
    assert "did:claimidx:agent-d" in dids
