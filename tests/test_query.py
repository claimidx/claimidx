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
