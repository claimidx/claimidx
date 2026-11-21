"""0.7.28 / Unreleased: weak-pass rerun-by, fail-precondition, cond, share preview, doctor trio."""

from __future__ import annotations

import json
import stat
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claimidx.fingerprint import fingerprint, fingerprint_accepted, normalize_error, normalization_risk
from claimidx.home import format_commons_review, parse_ledger
from claimidx.match import annotate, confirm_lights, hit_compact
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.public import projection_preview
from claimidx.sandbox import ReplayResult, _observe_py_detail
from claimidx.store import Store


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def _claim(**kw) -> Claim:
    err = kw.pop("err", "TypeError: proof claim")
    eco = kw.pop("eco", "py")
    rt = kw.pop("rt", _py_rt())
    dep = kw.pop("dep", [])
    cls = kw.pop("cls", "other")
    return Claim(
        fp=kw.pop("fp", fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep)),
        cls=cls,
        err=normalize_error(err),
        eco=eco,
        rt=rt,
        dep=dep,
        fix=kw.pop("fix", Fix(k="constraint", b="pin the known-good release")),
        eval=kw.pop("eval", EvalSpec(cmd="python -c 'import json'", expect=0)),
        own=kw.pop("own", "did:claimidx:agent-proof"),
        **kw,
    )


def test_ledger_regression_packaged_claims_keep_fingerprints():
    ledger = Path(__file__).resolve().parents[1] / "data" / "claims.jsonl"
    claims, skipped = parse_ledger(ledger.read_text(encoding="utf-8"))
    assert skipped == []
    assert claims
    for c in claims:
        assert fingerprint_accepted(c.fp, err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=list(c.dep or []))


def test_record_weak_pass_sets_rb_logs_event_and_leaves_counts(tmp_path: Path, monkeypatch):
    from claimidx.claim import record_weak_pass

    frozen = datetime(2026, 11, 21, 21, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("claimidx.claim.utcnow", lambda: frozen)
    store = Store(tmp_path / "ix.sqlite")
    c = _claim()
    store.put(c)
    rb = record_weak_pass(store, c.id, actor=c.own, reason="replay-net-unenforced")
    assert rb == frozen + timedelta(days=7)
    stored = store.get(c.id)
    assert stored is not None
    assert stored.rb == rb
    assert stored.nr == 0 and stored.nc == 0 and stored.nf == 0
    kinds = [e.get("kind") for e in store.events(limit=20) if e.get("claim_id") == c.id]
    assert "weak-pass" in kinds
    assert "fail" not in kinds


def test_rerun_days_env_and_confirm_lights(monkeypatch):
    from claimidx.claim import rerun_horizon

    frozen = datetime(2026, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setenv("CLAIMIDX_RERUN_DAYS", "3")
    monkeypatch.setattr("claimidx.claim.utcnow", lambda: frozen)
    assert rerun_horizon() == frozen + timedelta(days=3)
    rb = frozen + timedelta(days=3)
    before = confirm_lights(held=False, recorded=False, eval_passed=True, rerun_by=rb, now=frozen)
    assert before["recovery"] == "unrecorded"
    after = confirm_lights(held=False, recorded=False, eval_passed=True, rerun_by=rb, now=rb)
    assert after["recovery"] == "unchecked"
    # default (no rerun_by) stays unrecorded so existing tests keep passing
    assert confirm_lights(held=True, recorded=False)["recovery"] == "unrecorded"


def test_ask_lights_and_hit_compact_show_unchecked_after_rb():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    c = _claim(rb=past)
    q = {"err": c.err, "cls": c.cls, "eco": c.eco, "rt": c.rt, "dep": c.dep}
    ann = annotate(q, c, 1.0)
    assert ann["lights"]["recovery"] == "unchecked"
    row = hit_compact(q, c, 1.0)
    assert "rerun_by" in row


def test_condition_strength_values():
    from claimidx.gate import condition_strength

    enforced = ReplayResult(True, True, 0, 0, True, "held", net="offline", net_how="bwrap", interp_verified=True)
    assert condition_strength(enforced)["strength"] == "enforced"
    fake = ReplayResult(True, True, 0, 0, True, "held", net="offline", net_how="bwrap", interp_verified=False)
    assert condition_strength(fake)["strength"] == "best-effort"
    hook = ReplayResult(True, True, 0, 0, True, "held", net="offline", net_how="interpreter", interp_verified=True)
    assert condition_strength(hook)["strength"] == "best-effort"
    opened = ReplayResult(True, True, 0, 0, True, "held", net="open", net_how="", interp_verified=True)
    assert condition_strength(opened)["strength"] == "open"
    unenf = ReplayResult(True, True, 0, 0, True, "held", net="unenforced", net_how="python-flags", interp_verified=True)
    assert condition_strength(unenf)["strength"] == "unenforced"
    block = condition_strength(enforced, required=["net=off"])
    assert block["required"]["net"] == "off"
    assert block["actual"]["interp_verified"] is True


def test_observe_py_cpython_verified_and_fake_unverified(tmp_path: Path):
    env, verified = _observe_py_detail(sys.executable)
    assert env == _py_rt()
    assert verified is True

    fake = tmp_path / "fake-python"
    fake.write_text("#!/bin/sh\necho 9.9\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env, verified = _observe_py_detail(str(fake))
    assert env == "py@9.9"
    assert verified is False

    named = tmp_path / "named-python"
    named.write_text("#!/bin/sh\necho 'cpython 3.12'\n", encoding="utf-8")
    named.chmod(named.stat().st_mode | stat.S_IEXEC)
    env, verified = _observe_py_detail(str(named))
    assert env == "py@3.12"
    assert verified is True


def test_replay_fake_interpreter_is_best_effort(tmp_path: Path):
    from claimidx.gate import condition_strength

    fake = tmp_path / "python3"
    # A wrapper that only echoes a version, then runs the real interpreter for the eval itself.
    # _observe_py_detail calls `exe -c ...`; this script ignores -c and always prints 9.9.
    fake.write_text("#!/bin/sh\necho 9.9\n", encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    result = ReplayResult(True, True, 0, 0, True, "held", env="py@9.9", net="offline", net_how="bwrap", interpreter=str(fake), interp_verified=False)
    # as_dict carries cond
    info = result.as_dict()
    assert info["interp_verified"] is False
    assert info["cond"]["strength"] == "best-effort"
    assert condition_strength(result)["strength"] == "best-effort"


def test_verify_eval_precondition_is_fail_precondition_and_does_not_count(tmp_path: Path, capsys):
    from claimidx.cli import main
    from claimidx.query import ingest

    tree = tmp_path / "tree"
    tree.mkdir()
    db = tmp_path / "ix.sqlite"
    published = ingest(
        "TypeError: missing tree file",
        fix_k="constraint",
        fix_b="pin the known-good release",
        eval="python check.py",
        eco="py",
        rt=_py_rt(),
        db=db,
        share=False,
        cwd=str(tree),
    )
    cid = published["id"]
    rc = main(["--db", str(db), "--fmt", "json", "verify", "--apply", "--id", cid, "--cwd", str(tree)])
    cap = capsys.readouterr()
    assert rc == 2, cap.out + cap.err
    report = json.loads(cap.out)
    row = next(r for r in report["results"] if r["id"] == cid)
    assert row["action"] == "fail-precondition"
    assert str(row.get("reason") or "").startswith("eval-precondition")
    assert report["counts"].get("fail-precondition") == 1
    assert report["counts"].get("fail", 0) == 0
    stored = Store(db).get(cid)
    assert stored is not None
    assert stored.nf == 0 and stored.nr == 0
    assert "fail" not in [e.get("kind") for e in Store(db).events(limit=50) if e.get("claim_id") == cid]


def test_ask_grouped_normalization_risk(tmp_path: Path):
    from claimidx.query import ask, ingest

    db = tmp_path / "ix.sqlite"
    raw = "failed at /home/runner/app/src/page.tsx after 503 retries"
    ingest(
        raw,
        fix_k="constraint",
        fix_b="pin the known-good release",
        eval="python -c 'import json'",
        eco="other",
        db=db,
        share=False,
    )
    out = ask(raw, db=db)
    assert "normalization_risk" in out
    flags = set(out["normalization_risk"])
    assert "path" in flags
    assert flags == set(normalization_risk(raw))


def test_projection_preview_includes_redacted_payload_and_context():
    c = _claim(
        err="TypeError: private projection",
        fix=Fix(k="constraint", b="Inspect C:/Users/private/project/file.py and notify person@example.com"),
        eval=EvalSpec(cmd="python C:/Users/private/project/check.py", expect=0),
        tried=["C:/Users/private/project/attempt.py"],
        note="private local note about ticket TICKET-99 on host db.internal",
    )
    preview = projection_preview(c)
    assert preview["safe"] is True
    assert "note" in preview["removed"]
    assert "projection" in preview and "context" in preview
    assert preview["context"]["note"]
    # full payload, not a truncated slice
    assert preview["projection"]["id"] == c.id
    assert "err" in preview["projection"]
    assert "eval" in preview["projection"]


def test_format_commons_review_shows_full_payload_and_safeguard_note(monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    c = _claim(
        err="RuntimeError: review me with a long enough error string that would have been truncated at 160 characters if the preview still sliced it "
        + ("x" * 80),
        note="internal ticket ABC-123 at db.internal",
        tried=["./local-attempt.py"],
    )
    text = format_commons_review(c)
    assert "destination=commons" in text
    assert "full redacted public payload" in text
    assert "eval=" in text or '"eval"' in text
    assert "human preview is the real safeguard" in text
    assert "context stripped" in text or "note" in text
    # the long error should appear in the payload (not cut to 160)
    assert "long enough error string" in text


def test_doctor_lifts_version_trio(tmp_path: Path):
    from claimidx import __version__
    from claimidx.mcp_server import _call
    from claimidx.session import set_server_info_version

    set_server_info_version(__version__)
    store = Store(tmp_path / "ix.sqlite")
    out = _call("claimidx_doctor", {}, store)
    assert out["version"] == __version__
    assert out["package_version"] == __version__
    assert out["server_info_version"] == __version__
    assert out["version_mismatch"] is False
    from claimidx.mcp_server import TOOLS

    doctor = next(t for t in TOOLS if t["name"] == "claimidx_doctor")
    props = doctor["outputSchema"]["properties"]
    assert "package_version" in props and "server_info_version" in props and "version_mismatch" in props


def test_claim_rb_and_req_are_additive_defaults():
    c = _claim()
    assert c.rb is None
    assert c.req is None
    dumped = c.model_dump()
    assert "rb" in dumped and "req" in dumped
