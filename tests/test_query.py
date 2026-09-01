from claimidx import ask, ingest
from claimidx.cli import main


def test_python_ask_hits_seed(tmp_path):
    db = tmp_path / "ix.sqlite"
    assert main(["--db", str(db), "seed"]) == 0
    out = ask(
        "TypeError: params is a Promise",
        eco="npm",
        dep=["next@15.0.0"],
        db=db,
    )
    assert out["hit"] is True
    assert out["n"] >= 1
    assert out["claims"][0]["id"] == "spr_a11c000000000001"
    assert "warn" in out["claims"][0]


def test_python_ask_miss(tmp_path):
    db = tmp_path / "ix.sqlite"
    out = ask("definitely-not-a-known-xyzzy-error-string", eco="py", db=db)
    assert out["hit"] is False
    assert out["n"] == 0
    assert out["claims"] == []


def test_python_ingest_is_local_and_does_not_share(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:test")
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    db = tmp_path / "ix.sqlite"
    out = ingest(
        "ModuleNotFoundError: No module named 'qwen_mod'",
        fix_k="pin",
        fix_b="pip install qwen-mod",
        eval="true",
        eco="py",
        db=db,
    )
    assert out["exists"] is False
    assert out["id"].startswith("cix_")
    assert "share" not in out
    again = ingest(
        "ModuleNotFoundError: No module named 'qwen_mod'",
        fix_k="pin",
        fix_b="pip install qwen-mod",
        eval="true",
        eco="py",
        db=db,
    )
    assert again["exists"] is True
    assert again["id"] == out["id"]


def test_python_ingest_stores_expect(tmp_path, monkeypatch):
    """CLI ingest --expect. from claimidx import ingest must store eval.expect."""
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:test")
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    db = tmp_path / "ix.sqlite"
    out = ingest(
        "python -c exit 1 is the proof",
        fix_k="cmd",
        fix_b="python -c \"import sys; sys.exit(1)\"",
        eval="python -c \"import sys; sys.exit(1)\"",
        expect=1,
        eco="py",
        db=db,
    )
    assert out["exists"] is False
    c = Store(db).get(out["id"])
    assert c is not None
    assert c.eval.expect == 1


def test_python_verify_defaults_to_dry_run(tmp_path, monkeypatch):
    """CLI and MCP can dry-run. In-process from claimidx import verify must exist and default dry_run."""
    import subprocess
    from claimidx import verify

    calls: list = []

    def _blocked(*a, **k):
        calls.append(a)
        raise AssertionError(f"subprocess during python verify dry-run: {a}")

    monkeypatch.setattr(subprocess, "run", _blocked)
    monkeypatch.setattr("claimidx.replay.subprocess.run", _blocked)
    out = verify(k=1, db=tmp_path / "ix.sqlite")
    assert out["dry_run"] is True
    assert calls == []
