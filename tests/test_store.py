from pathlib import Path

from claimidx.dense import decode, encode
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def _claim() -> Claim:
    err = "TypeError: params is a Promise"
    return Claim(
        fp=fingerprint(err=err, eco="npm", rt="node@20", dep=["next@15.0.0"]),
        cls="async_api", err=normalize_error(err), eco="npm", rt="node@20", dep=["next@15.0.0"],
        fix=Fix(k="patch", b="const { slug } = await params"), eval=EvalSpec(cmd="npx tsc --noEmit"),
        own="did:claimidx:test",
    )


def test_put_get_confirm_fail(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim())
    assert store.get(c.id).id == c.id
    assert store.confirm(c.id).st == "confirmed"
    assert store.fail(c.id).nf == 1


def test_two_fails_contest(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim())
    store.fail(c.id)
    assert store.fail(c.id).st == "contested"


def test_migrates_v01_denormalized_table(tmp_path: Path):
    import json
    import sqlite3

    path = tmp_path / "legacy.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE claims (
            id TEXT PRIMARY KEY, fp TEXT NOT NULL, cls TEXT NOT NULL, err TEXT NOT NULL,
            eco TEXT, rt TEXT, dep TEXT, tried TEXT, fix_k TEXT, fix_b TEXT,
            eval_cmd TEXT, eval_expect INTEGER, st TEXT, nc INTEGER, nf INTEGER,
            note TEXT, created_at REAL, updated_at REAL, last_confirm_at REAL
        )
        """
    )
    err = "TypeError: params is a Promise"
    fp = fingerprint(err=err, eco="npm", rt="node@20", dep=["next@15.0.0"])
    con.execute(
        "INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "spr_aaaaaaaaaaaaaaaa", fp, "async_api", err, "npm", "node@20",
            json.dumps(["next@15.0.0"]), json.dumps(["sync-access"]),
            "patch", "const { slug } = await params", "true", 0,
            "confirmed", 1, 0, "", 1787989331.2, 1787989331.2, 1787989331.2,
        ),
    )
    con.commit()
    con.close()
    store = Store(path)
    got = store.get("spr_aaaaaaaaaaaaaaaa")
    assert got is not None
    assert got.fix.b.startswith("const")
    assert got.src == "seed"


def test_reject_is_terminal(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim())
    rejected = store.reject(c.id, "did:claimidx:test")
    assert rejected.st == "rejected"
    again = store.get(c.id)
    assert again is not None and again.st == "rejected"


def test_force_reset_event_commits_before_replace(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim())
    reset = {"nr": 1, "nc": 2, "nf": 0, "rt": "py@3.12"}

    def boom(claim):
        raise RuntimeError("crash after event")

    store.put = boom  # type: ignore[method-assign]
    import pytest

    with pytest.raises(RuntimeError, match="crash after event"):
        store.publish(c, c.own, reset)
    rows = [e for e in Store(tmp_path / "ix.sqlite").events() if e["kind"] == "force_reset"]
    assert len(rows) == 1
    assert rows[0]["detail"] == reset
    shown = Store(tmp_path / "ix.sqlite").get(c.id)
    assert shown is not None
    assert shown.fix.b.startswith("const")


def test_force_reset_is_an_events_row(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim())
    reset = {"nr": 1, "nc": 1, "nf": 0, "rt": "py@3.12"}
    store.log_force_reset(c.own, c.id, reset)
    store.log_force_reset(c.own, c.id, {"nr": 0, "nc": 0, "nf": 0, "rt": ""})
    rows = [e for e in store.events() if e["kind"] == "force_reset"]
    assert len(rows) == 1
    assert rows[0]["claim_id"] == c.id
    assert rows[0]["detail"] == reset


def test_events_detail_migrates_from_legacy_table(tmp_path: Path):
    import sqlite3

    path = tmp_path / "legacy-events.sqlite"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, kind TEXT, actor TEXT, ts TEXT)"
    )
    con.execute(
        "INSERT INTO events(claim_id, kind, actor, ts) VALUES (?,?,?,?)",
        ("spr_aaaaaaaaaaaaaaaa", "publish", "did:claimidx:test", "2026-08-30T00:00:00+00:00"),
    )
    con.commit()
    con.close()
    store = Store(path)
    store.log("force_reset", "did:claimidx:test", "spr_aaaaaaaaaaaaaaaa", detail={"nr": 2, "nc": 2, "nf": 0, "rt": "py@3.12"})
    kinds = {e["kind"]: e for e in store.events()}
    assert kinds["publish"]["kind"] == "publish"
    assert "detail" not in kinds["publish"]
    assert kinds["force_reset"]["detail"] == {"nr": 2, "nc": 2, "nf": 0, "rt": "py@3.12"}


def test_id_must_be_hex():
    import pytest
    from pydantic import ValidationError
    from claimidx.models import Claim

    with pytest.raises((ValidationError, ValueError)):
        Claim(
            id="spr_zzzzzzzzzzzzzzzz",
            fp="ab" * 32,
            cls="other",
            err="x",
            fix=Fix(k="constraint", b="ok"),
            eval=EvalSpec(cmd="true"),
            own="did:claimidx:test",
        )


def test_import_jsonl_skips_bad_lines(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    path = tmp_path / "mix.jsonl"
    good = store.put(_claim()).model_dump_json()
    path.write_text("not-json\n" + good + "\n{}\n", encoding="utf-8")
    n = store.import_jsonl(path)
    assert n >= 1


def test_dense_roundtrip():
    c = _claim()
    back = decode(encode(c))
    assert back.id == c.id and back.fp == c.fp and back.fix.b == c.fix.b
