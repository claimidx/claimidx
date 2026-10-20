"""Local session continuity — ask/ingest/fail memory in the index DB."""

from claimidx.cli import main
from claimidx.query import ask, ingest
from claimidx.store import Store


def test_session_records_ask_and_flags_must_ask_after_repeated_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:session-test")
    monkeypatch.setenv("CLAIMIDX_SESSION", "sess_test_1")
    db = tmp_path / "ix.sqlite"
    err = "ModuleNotFoundError: No module named 'session_demo'"
    miss = ask(err, eco="py", db=db)
    assert miss["hit"] is False
    assert "session" in miss
    assert miss["session"]["session_id"] == "sess_test_1"
    assert miss["session"]["asks"] >= 1

    written = ingest(
        err,
        fix_k="pin",
        fix_b="session-demo>=1",
        eval='python -c "import session_demo"',
        eco="py",
        db=db,
    )
    cid = written["id"]
    store = Store(db)
    summary = store.session_summary("sess_test_1")
    assert any(row.get("claim_id") == cid for row in summary.get("ingests") or [])

    assert main(["--db", str(db), "fail", cid, "--own", "did:claimidx:session-test"]) == 0
    assert main(["--db", str(db), "fail", cid, "--own", "did:claimidx:session-test"]) == 0
    again = ask(err, eco="py", db=db)
    assert again["session"]["must_ask"] is True
    assert again["session"]["fails_by_fp"].get(again["fp"], 0) >= 2


def test_ingest_draft_promote_and_alternatives(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:draft-test")
    db = tmp_path / "ix.sqlite"
    err = "ModuleNotFoundError: No module named 'draft_demo'"
    assert (
        main(
            [
                "--db",
                str(db),
                "--fmt",
                "json",
                "ingest-draft",
                "--err",
                err,
                "--fix-k",
                "pin",
                "--fix-b",
                "draft-demo>=1",
                "--eval",
                "true",
                "--eco",
                "py",
            ]
        )
        == 0
    )
    # promote via second call using draft id from store
    from claimidx.drafts import get_draft
    from claimidx.store import Store

    store = Store(db)
    with store._conn() as con:
        row = con.execute("SELECT id FROM drafts LIMIT 1").fetchone()
    assert row
    draft_id = row["id"]
    assert get_draft(store, draft_id) is not None
    assert main(["--db", str(db), "--fmt", "json", "ingest-draft", "--promote", draft_id]) == 0
    claims = store.all()
    assert len(claims) == 1
    # alternative + fail --against writes contradicts
    assert (
        main(
            [
                "--db",
                str(db),
                "ingest",
                "--err",
                err,
                "--fix-k",
                "wontfix",
                "--fix-b",
                "upstream; wait",
                "--eval",
                "true",
                "--eco",
                "py",
                "--alternative",
            ]
        )
        == 0
    )
    ids = [c.id for c in store.all()]
    assert len(ids) == 2
    assert main(["--db", str(db), "fail", ids[0], "--against", ids[1]]) == 0
    assert main(["--db", str(db), "--fmt", "json", "alternatives", ids[0]]) == 0


def test_doctor_cwd_reports_tree_markers(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:tree-test")
    db = tmp_path / "ix.sqlite"
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    main(["--db", str(db), "doctor", "--cwd", str(tmp_path)])
    out = capsys.readouterr().out
    assert "tree-markers" in out
    assert "package.json" in out
    assert "session" in out
