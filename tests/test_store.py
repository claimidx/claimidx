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
