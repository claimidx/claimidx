from pathlib import Path

from claimidx.cli import main
from claimidx.team import resolve_owner, whoami


def test_resolve_owner_env(monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:harper")
    assert resolve_owner() == "did:claimidx:harper"
    assert whoami()["wired"] is True
    assert whoami()["agent"] == "harper"


def test_resolve_owner_agent_name(monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.setenv("CLAIMIDX_AGENT", "lucas")
    assert resolve_owner() == "did:claimidx:lucas"


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
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:benjamin")
    assert main(["--db", db, "whoami"]) == 0
    assert "did:claimidx:benjamin" in capsys.readouterr().out
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
    assert "did:claimidx:benjamin" in out
    assert "publish" in out
