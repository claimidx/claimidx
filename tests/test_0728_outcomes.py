"""Unreleased: write outcomes, distinct, env-deleted, field-set upgrade skip reason."""

from __future__ import annotations

import json
import sys
from pathlib import Path


from claimidx.distinct import compare, split_argv
from claimidx.fieldset import UPGRADE_HINT, fieldset_rejection
from claimidx.fingerprint import classify, fingerprint, fingerprint_accepted, same_fingerprint
from claimidx.gate import graduation_gate, status_only_eval
from claimidx.home import parse_ledger, parse_ledger_report, pull, pull_skip_lines
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.public import ingest_warnings
from claimidx.sandbox import ReplayResult, replay_records_hold
from claimidx.store import Store
from claimidx.writeout import (
    DEDUPE_HELD,
    DEDUPE_MISSED,
    WRITE_LOST,
    WRITE_UNVERIFIED,
    write_outcome,
)


LEDGER = Path(__file__).resolve().parents[1] / "data" / "claims.jsonl"
WRITE_201 = "POST /items HTTP 201 created, accepted but not visible on re-read"


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def _held(cmd: str = 'python -c "import json"') -> ReplayResult:
    return ReplayResult(True, True, 0, 0, True, "held", env=_py_rt(), interpreter=sys.executable)


def _claim(*, err: str, cls: str, ev: str = 'python -c "import json"', **kw) -> Claim:
    eco = kw.pop("eco", "py")
    rt = kw.pop("rt", _py_rt() if eco == "py" else "")
    dep = kw.pop("dep", [])
    return Claim(
        fp=kw.pop("fp", None) or fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep),
        cls=cls,
        err=err,
        eco=eco,
        rt=rt,
        dep=dep,
        fix=kw.pop("fix", Fix(k="constraint", b="wait and re-read the consumer listing")),
        eval=kw.pop("eval", EvalSpec(cmd=ev, expect=0)),
        own=kw.pop("own", "did:claimidx:agent-outcomes"),
        **kw,
    )


def test_ledger_regression_packaged_claims_keep_fingerprints():
    raw_lines = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    stored = {row["id"]: row["fp"] for row in raw_lines}
    claims, skipped = parse_ledger(LEDGER.read_text(encoding="utf-8"))
    assert skipped == []
    assert len(claims) == len(stored)
    for c in claims:
        assert c.fp == stored[c.id]
        assert fingerprint_accepted(c.fp, err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=list(c.dep or []))
        # New classify rules must not swallow packaged ledger rows.
        assert classify(c.err) not in {WRITE_UNVERIFIED, "stale_identical"}


def test_http_status_lie_rows_keep_class_and_fingerprint():
    raw_lines = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    claims, skipped = parse_ledger(LEDGER.read_text(encoding="utf-8"))
    assert skipped == []
    by_id = {c.id: c for c in claims}
    for row in raw_lines:
        if row.get("cls") != "http_status_lie":
            continue
        c = by_id[row["id"]]
        assert c.cls == "http_status_lie"
        assert c.fp == row["fp"]
        assert fingerprint_accepted(c.fp, err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=list(c.dep or []))
    # A clean 200 lie still classifies as http_status_lie; the new 201 rule must not steal it.
    assert classify("HTTP 200 but the body is an error page") == "http_status_lie"
    assert classify("status code 200") == "http_status_lie"


def test_write_unverified_classifies_201_but_not_200():
    assert classify(WRITE_201) == WRITE_UNVERIFIED
    assert classify("HTTP 201 already exists") == WRITE_UNVERIFIED
    assert classify("accepted but unverified") == WRITE_UNVERIFIED
    assert classify("HTTP 200 but the body is an error page") == "http_status_lie"


def test_write_outcome_splits_fingerprints_and_actions():
    lost = write_outcome(0)
    held = write_outcome(1)
    missed = write_outcome(3)
    assert lost["cls"] == WRITE_LOST and lost["write_outcome"] == "write-lost" and lost["action"] == "retry"
    assert held["cls"] == DEDUPE_HELD and held["write_outcome"] == "dedupe-held" and held["action"] == "stop"
    assert missed["cls"] == DEDUPE_MISSED and missed["write_outcome"] == "dedupe-missed"
    fps = {
        name: fingerprint(err=WRITE_201, cls=body["cls"], eco="py")
        for name, body in (("lost", lost), ("held", held), ("missed", missed), ("unverified", {"cls": WRITE_UNVERIFIED}))
    }
    assert len(set(fps.values())) == 4
    # A stop-retrying fix must never exact-match a lost write.
    assert not same_fingerprint(
        query_err=WRITE_201,
        query_cls=DEDUPE_HELD,
        query_eco="py",
        claim_err=WRITE_201,
        claim_cls=WRITE_LOST,
        claim_eco="py",
    )


def test_write_outcomes_never_mint_nr(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    ev = "python -c \"print('%{http_code}')\""
    for cls in (WRITE_UNVERIFIED, WRITE_LOST, DEDUPE_HELD, DEDUPE_MISSED):
        c = _claim(err=WRITE_201, cls=cls, ev=ev)
        store.put(c)
        decision = graduation_gate(c, _held(ev), cwd=str(tmp_path), store=store, actor=c.own)
        assert decision.mint_nr is False
        assert decision.record is True
        assert "not proof" in decision.reason
        assert any("status code" in w for w in decision.warns)
        confirmed = store.confirm(
            c.id,
            c.own,
            replayed=False,
            detail={"write_outcome": write_outcome(0 if cls == WRITE_LOST else 1 if cls == DEDUPE_HELD else 3 if cls == DEDUPE_MISSED else 0)["write_outcome"]},
        )
        assert confirmed.nr == 0
        assert confirmed.nc >= 1


def test_status_only_eval_warns_http_status_lie_but_does_not_refuse(tmp_path: Path):
    ev = "python -c \"print('%{http_code}')\""
    assert status_only_eval(ev)
    assert status_only_eval("curl -s -o /dev/null -w %{http_code} https://example.invalid/health")
    c = _claim(err="HTTP 200 but the body is an error page", cls="http_status_lie", ev=ev)
    store = Store(tmp_path / "ix.sqlite")
    store.put(c)
    decision = graduation_gate(c, _held(ev), cwd=str(tmp_path), store=store, actor=c.own)
    # Warning only: do not refuse nr for http_status_lie.
    assert any("status code" in w for w in decision.warns)
    assert decision.mint_nr is True or (decision.mint_nr is False and "target" in (decision.reason or ""))
    warns = ingest_warnings(c.err, ev, cls="http_status_lie")
    assert any("status code" in w for w in warns)


def test_distinct_exits_and_empty_not_identical():
    assert split_argv(["--", "echo", "a", ":::", "echo", "b"]) == (["echo", "a"], ["echo", "b"])
    differ = compare([sys.executable, "-c", "print('alpha')"], [sys.executable, "-c", "print('beta')"])
    assert differ["exit"] == 0 and differ["result"] == "distinct" and not differ["stale_identical"]
    same = compare([sys.executable, "-c", "print('same')"], [sys.executable, "-c", "print('same')"])
    assert same["exit"] == 1 and same["stale_identical"] is True and same["result"] == "stale_identical"
    empty = compare([sys.executable, "-c", "pass"], [sys.executable, "-c", "print('x')"])
    assert empty["exit"] == 2 and empty["result"] == "empty-result"
    assert empty["stale_identical"] is False
    both = compare([sys.executable, "-c", "pass"], [sys.executable, "-c", "pass"])
    assert both["exit"] == 2 and both["stale_identical"] is False


def test_distinct_cli(tmp_path: Path, capsys):
    from claimidx.cli import main

    db = str(tmp_path / "ix.sqlite")
    rc = main(
        [
            "--db",
            db,
            "distinct",
            "--",
            sys.executable,
            "-c",
            "print('x')",
            ":::",
            sys.executable,
            "-c",
            "print('x')",
        ]
    )
    out = capsys.readouterr()
    assert rc == 1
    body = json.loads(out.out)
    assert body["stale_identical"] is True
    assert "stale_identical" in out.err


def test_write_outcome_cli(capsys):
    from claimidx.cli import main

    rc = main(["write-outcome", "--copies", "1", "--err", WRITE_201])
    out = capsys.readouterr()
    assert rc == 0
    body = json.loads(out.out)
    assert body["cls"] == DEDUPE_HELD and body["action"] == "stop"
    assert body["classify"] == WRITE_UNVERIFIED
    assert body["fp"] != body["fp_unverified"]


def test_uv_env_deleted_is_explicit(tmp_path: Path):
    missing_uv = tmp_path / "share" / "uv" / "python" / "cpython-3.13" / "bin" / "python"
    # Path is absent on purpose.
    ok, why = replay_records_hold(
        _py_rt(),
        _held(),
        'python -c "import json"',
        captured_interpreter=str(missing_uv),
        cwd=str(tmp_path),
    )
    assert ok is False
    assert "env-deleted" in why
    assert "gone" not in why
    # A leftover pyvenv.cfg that mentions uv also counts, even off the share/uv path.
    venv_py = tmp_path / "myenv" / "bin" / "python"
    (tmp_path / "myenv").mkdir()
    (tmp_path / "myenv" / "pyvenv.cfg").write_text("home = /opt\nuv = 0.5.0\n", encoding="utf-8")
    ok2, why2 = replay_records_hold(
        _py_rt(),
        _held(),
        'python -c "import json"',
        captured_interpreter=str(venv_py),
        cwd=str(tmp_path),
    )
    assert ok2 is False
    assert "env-deleted" in why2
    # Non-uv missing path still reports the generic gone-interpreter reason.
    missing = tmp_path / "gone" / "python"
    ok3, why3 = replay_records_hold(
        _py_rt(),
        _held(),
        'python -c "import json"',
        captured_interpreter=str(missing),
        cwd=str(tmp_path),
    )
    assert ok3 is False
    assert "gone" in why3
    assert "env-deleted" not in why3


def test_unknown_fieldset_version_tells_user_to_upgrade():
    err = "TypeError: future field-set must be skipped"
    raw = json.loads(_claim(err=err, cls="type_error").model_dump_json())
    future = dict(raw)
    future["v"] = 99
    reason = fieldset_rejection(future)
    assert reason == "unknown field-set version v99, upgrade claimidx"
    _, skipped = parse_ledger(json.dumps(future) + "\n")
    assert skipped and "unknown field-set version v99, upgrade claimidx" in skipped[0]


def test_pull_and_cli_surface_upgrade_skip(tmp_path: Path, monkeypatch, capsys):
    raw = json.loads(_claim(err="TypeError: skip me", cls="type_error").model_dump_json())
    raw["v"] = 99

    def fake_fetch(url=None):
        claims, skipped, excluded = parse_ledger_report(json.dumps(raw) + "\n")
        return claims, skipped, excluded, "https://example.invalid/claims.jsonl"

    monkeypatch.setattr("claimidx.home.fetch_ledger_report", fake_fetch)
    store = Store(tmp_path / "ix.sqlite")
    result = pull(store)
    assert result["skipped_n"] == 1
    assert "unknown field-set version v99, upgrade claimidx" in result["skipped"][0]
    assert result["hint"] == UPGRADE_HINT
    lines = "\n".join(pull_skip_lines(result))
    assert "unknown field-set version v99, upgrade claimidx" in lines
    assert "upgrade claimidx" in lines

    from claimidx.cli import main

    monkeypatch.setattr("claimidx.home.pull", lambda *a, **k: result)
    rc = main(["--db", str(tmp_path / "ix2.sqlite"), "pull"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "unknown field-set version v99, upgrade claimidx" in captured.err
    assert "upgrade claimidx" in captured.err


def test_mcp_home_pull_returns_upgrade_skip(tmp_path: Path, monkeypatch):
    from claimidx.mcp_server import handle

    raw = json.loads(_claim(err="TypeError: skip me", cls="type_error").model_dump_json())
    raw["v"] = 99

    def fake_fetch(url=None):
        claims, skipped, excluded = parse_ledger_report(json.dumps(raw) + "\n")
        return claims, skipped, excluded, "https://example.invalid/claims.jsonl"

    monkeypatch.setattr("claimidx.home.fetch_ledger_report", fake_fetch)
    store = Store(tmp_path / "ix.sqlite")
    listed = handle({"id": 1, "method": "tools/list"}, store)
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    desc = tools["claimidx_home_pull"]["description"]
    assert "unknown field-set version vN, upgrade claimidx" in desc
    props = tools["claimidx_home_pull"]["outputSchema"]["properties"]
    assert "skipped" in props and "hint" in props and "skipped_n" in props
    called = handle({"id": 2, "method": "tools/call", "params": {"name": "claimidx_home_pull", "arguments": {}}}, store)
    content = called["result"].get("structuredContent") or called["result"]
    skipped = content.get("skipped") or []
    assert any("unknown field-set version v99, upgrade claimidx" in str(s) for s in skipped)
    assert "upgrade claimidx" in (content.get("hint") or "")


def test_no_v2_literal_widening_or_new_relation_kinds():
    from typing import get_args

    from claimidx.graph import ProofOp, Relation

    # Do not widen closed ProofOp or Relation.kind Literals in this PR.
    assert "expect_distinct" not in get_args(ProofOp)
    kind = Relation.model_fields["kind"].annotation
    args = get_args(kind) or get_args(getattr(kind, "__args__", ()) or ())
    # Relation.kind stays the closed set already on main; no new kind for write outcomes.
    assert "write_outcome" not in {str(a) for a in args}
