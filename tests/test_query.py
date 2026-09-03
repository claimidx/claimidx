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
    assert out.get("near") == []
    assert isinstance(out.get("near_why"), list)
    assert out.get("dead_ends") == []


def test_python_ask_miss_includes_near_and_dead_ends(tmp_path, monkeypatch):
    from claimidx.fingerprint import fingerprint, normalize_error
    from claimidx.models import Claim, EvalSpec, Fix
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:test")
    db = tmp_path / "ix.sqlite"
    store = Store(db)
    err_near = "TypeError: widget is a Promise"
    # Same eco/class-ish shape as a next/params claim but different enough to miss at default threshold
    # when ranked against a below-threshold cousin — seed a weak cousin + a dead end on another fp.
    weak = Claim(
        fp=fingerprint(err=err_near, cls="async_api", eco="npm", rt="node@20", dep=["next@14.0.0"]),
        cls="async_api",
        err=normalize_error(err_near),
        eco="npm",
        rt="node@20",
        dep=["next@14.0.0"],
        fix=Fix(k="patch", b="await widget"),
        eval=EvalSpec(cmd="npx tsc --noEmit"),
        own="did:claimidx:test",
        st="proposed",
        src="local",
    )
    store.put(weak)
    dead_err = "TypeError: params is a Promise"
    dead = Claim(
        fp=fingerprint(err=dead_err, cls="async_api", eco="npm", rt="node@20", dep=["next@15.0.0"]),
        cls="async_api",
        err=normalize_error(dead_err),
        eco="npm",
        rt="node@20",
        dep=["next@15.0.0"],
        fix=Fix(k="wontfix", b="upstream; wait for next major"),
        eval=EvalSpec(cmd="true"),
        own="did:claimidx:test",
        st="confirmed",
        src="local",
    )
    store.put(dead)
    contested = Claim(
        fp=dead.fp,
        cls="async_api",
        err=normalize_error(dead_err),
        eco="npm",
        rt="node@20",
        dep=["next@15.0.0"],
        fix=Fix(k="patch", b="await params"),
        eval=EvalSpec(cmd="npx tsc --noEmit"),
        own="did:claimidx:test",
        st="contested",
        nf=2,
        src="local",
    )
    store.put(contested)

    # Query that misses default rank but may surface weak as near; dead_ends from same-fp family when querying dead_err with deps that zero similarity to dead?
    # Query the dead_err with disjoint deps so primary rank misses, but dead_ends still find same-fp contested/wontfix via store fp lookup.
    out = ask(dead_err, eco="npm", dep=["unrelated@1.0.0"], db=db)
    assert out["hit"] is False
    assert out["claims"] == []
    ends = out.get("dead_ends") or []
    assert any(row.get("fix", {}).get("k") == "wontfix" or row.get("st") == "contested" for row in ends)
    assert "dead_end" in (out.get("near_why") or []) or ends


def test_python_ask_logs_hit_miss_and_ms(tmp_path, monkeypatch):
    """Ask events must record hit/miss and retrieve ms. Empty detail cannot estimate savings."""
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:test")
    db = tmp_path / "ix.sqlite"
    assert main(["--db", str(db), "seed"]) == 0
    assert ask("TypeError: params is a Promise", eco="npm", dep=["next@15.0.0"], db=db)["hit"] is True
    assert ask("definitely-not-a-known-xyzzy-error-string", eco="py", db=db)["hit"] is False
    rows = [e for e in Store(db).events(limit=20) if e["kind"] == "ask"]
    assert len(rows) >= 2
    by_hit = {e["detail"]["hit"]: e["detail"] for e in rows}
    assert True in by_hit and False in by_hit
    assert by_hit[True]["n"] >= 1
    assert by_hit[False]["n"] == 0
    assert isinstance(by_hit[True]["ms"], int) and by_hit[True]["ms"] >= 0
    assert isinstance(by_hit[False]["ms"], int) and by_hit[False]["ms"] >= 0
    stats = Store(db).stats()
    assert stats["asks"] >= 2
    assert stats["ask_hits"] >= 1
    assert stats["ask_misses"] >= 1


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
        fix_b='python -c "import sys; sys.exit(1)"',
        eval='python -c "import sys; sys.exit(1)"',
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
