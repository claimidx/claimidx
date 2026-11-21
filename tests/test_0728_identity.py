"""Unreleased: rule-ID catalog, fp_alias edges, field-set rejection on pull."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claimidx.fingerprint import (
    canonical_error_id,
    fingerprint,
    fingerprint_accepted,
    fingerprints,
    legacy_fingerprint_material,
    remap_rule_id,
    same_fingerprint,
    v0724_fingerprint_material,
    _hash_material,
)
from claimidx.home import parse_ledger, pull
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def _claim(*, err: str, eco: str = "py", fp: str | None = None, **kw) -> Claim:
    cls = kw.pop("cls", "other")
    rt = kw.pop("rt", _py_rt() if eco == "py" else "")
    dep = kw.pop("dep", [])
    return Claim(
        fp=fp or fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep),
        cls=cls,
        err=err,
        eco=eco,
        rt=rt,
        dep=dep,
        fix=kw.pop("fix", Fix(k="constraint", b="pin the known-good release")),
        eval=kw.pop("eval", EvalSpec(cmd="python -c 'import json'", expect=0)),
        own=kw.pop("own", "did:claimidx:agent-identity"),
        **kw,
    )


def _line(claim: Claim) -> str:
    return json.dumps(json.loads(claim.model_dump_json()), default=str)


def test_ledger_regression_packaged_claims_keep_fingerprints():
    ledger = Path(__file__).resolve().parents[1] / "data" / "claims.jsonl"
    raw_lines = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    stored = {row["id"]: row["fp"] for row in raw_lines}
    claims, skipped = parse_ledger(ledger.read_text(encoding="utf-8"))
    assert skipped == []
    assert len(claims) == len(stored)
    for c in claims:
        assert c.fp == stored[c.id]
        assert fingerprint_accepted(c.fp, err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=list(c.dep or []))


def test_canonical_error_id_new_shapes_and_bare_integers():
    assert canonical_error_id("PLR0913 Too many arguments") == "PLR0913"
    assert canonical_error_id("src/app.py:1:0: PLR0915 too-many-statements") == "PLR0915"
    assert canonical_error_id("UP007 Use X | Y") == "UP007"
    assert canonical_error_id("B008 Do not perform function calls") == "B008"
    assert canonical_error_id("ANN001 missing type") == "ANN001"
    assert canonical_error_id("'x' is assigned a value but never used  no-unused-vars", eco="npm") == "no-unused-vars"
    assert canonical_error_id("'x' is assigned a value but never used  @typescript-eslint/no-unused-vars", eco="npm") == "@typescript-eslint/no-unused-vars"
    # eco must be npm; a Python failure that happens to end with a hyphenated phrase is not an ESLint ID
    assert canonical_error_id("'x' is assigned a value but never used  no-unused-vars", eco="py") is None
    assert canonical_error_id("exit 1") is None
    assert canonical_error_id("HTTP 200") is None
    assert canonical_error_id("status code: 404") is None


def test_rule_id_map_remaps_old_to_canonical():
    assert remap_rule_id("PLR1701") == "SIM101"
    assert remap_rule_id("no-native-reassign") == "no-global-assign"
    assert canonical_error_id("PLR1701 Consider merging") == "SIM101"
    assert canonical_error_id("do not reassign natives  no-native-reassign", eco="npm") == "no-global-assign"
    # unmapped IDs stay themselves; different PLR rules stay distinct
    assert canonical_error_id("PLR0913 too many arguments") == "PLR0913"
    assert canonical_error_id("PLR0917 too many positional") == "PLR0917"
    assert canonical_error_id("PLR0913 too many arguments") != canonical_error_id("PLR0917 too many positional")


def test_fingerprints_are_current_legacy_and_remapped():
    err = "PLR0913 Too many arguments in f()"
    fps = fingerprints(err=err, eco="py")
    current = fingerprint(err=err, eco="py")
    legacy = _hash_material(legacy_fingerprint_material(err=err, eco="py"))
    mid = _hash_material(v0724_fingerprint_material(err=err, eco="py"))
    assert fps[0] == current
    assert current != legacy
    assert mid == legacy  # 0.7.24 did not recognize PLR0913, so it hashed wording
    assert legacy in fps
    assert fingerprint_accepted(legacy, err=err, eco="py")
    # A mapped ID produces a third hash (pre-map) distinct from current and wording
    mapped = "PLR1701 Consider merging isinstance"
    trio = fingerprints(err=mapped, eco="py")
    assert trio[0] == fingerprint(err=mapped, eco="py")
    assert canonical_error_id(mapped) == "SIM101"
    assert len(trio) == 3
    assert fingerprint_accepted(trio[2], err=mapped, eco="py")


def test_negative_fixture_same_wording_different_rule_ids_do_not_match():
    a = "error TS2307: Cannot find module 'foo'"
    b = "error TS2304: Cannot find module 'foo'"
    assert canonical_error_id(a) == "TS2307"
    assert canonical_error_id(b) == "TS2304"
    fps_a = set(fingerprints(err=a, eco="npm"))
    fps_b = set(fingerprints(err=b, eco="npm"))
    assert fps_a.isdisjoint(fps_b)
    assert not same_fingerprint(query_err=a, query_eco="npm", claim_err=b, claim_eco="npm")
    p = "PLR0913 Too many arguments for 'f'"
    q = "PLR0917 Too many arguments for 'f'"
    assert set(fingerprints(err=p, eco="py")).isdisjoint(set(fingerprints(err=q, eco="py")))
    assert not same_fingerprint(query_err=p, query_eco="py", claim_err=q, claim_eco="py")


def test_fp_alias_edges_used_by_lookup_not_by_negative_pair(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    err = "PLR0913 Too many arguments in g()"
    fields = dict(cls="other", eco="py", rt=_py_rt(), dep=[])
    legacy = _hash_material(legacy_fingerprint_material(err=err, **fields))
    # Ingest a ≤0.7.26 row that stored the wording hash
    old = _claim(err=err, fp=legacy, **fields)
    assert old.fp == legacy
    store.put(old)
    current = fingerprint(err=err, **fields)
    assert current != legacy
    found = store.by_fingerprints((current,))
    assert [c.id for c in found] == [old.id]
    # Negative pair must not share an edge
    other = _claim(err="PLR0917 Too many positional in g()", eco="py")
    store.put(other)
    with store._conn() as con:
        rows = con.execute("SELECT alias_fp, canonical_fp FROM fp_alias").fetchall()
    aliases = {(r["alias_fp"], r["canonical_fp"]) for r in rows}
    other_fps = set(fingerprints(err=other.err, eco="py")) | {other.fp}
    old_fps = set(fingerprints(err=err, eco="py")) | {old.fp}
    assert other_fps.isdisjoint(old_fps)
    for a, c in aliases:
        assert not (a in old_fps and c in other_fps)
        assert not (a in other_fps and c in old_fps)


def test_pull_round_trip_accepts_0726_format(tmp_path: Path, monkeypatch):
    """A ≤0.7.26 ledger line (wording hash for a then-unrecognized PLR id, plus a 0.7.24 TS id hash) pulls and looks up."""
    ledger = tmp_path / "home.jsonl"
    plr_err = "PLR0913 Too many arguments on the public API"
    ts_err = "error TS2307: Cannot find module 'foo'"
    plr_fields = dict(cls="other", eco="py", rt=_py_rt(), dep=[])
    ts_fields = dict(cls="other", eco="npm", rt="node@20", dep=[])
    # 0.7.26 stored the wording hash for PLR0913 (no catalog entry yet)
    plr_fp = _hash_material(legacy_fingerprint_material(err=plr_err, **plr_fields))
    # 0.7.26 already hashed TS2307 by ID
    ts_fp = _hash_material(v0724_fingerprint_material(err=ts_err, **ts_fields))
    plr = _claim(err=plr_err, fp=plr_fp, id="cix_0123456789abcdef", **plr_fields)
    ts = _claim(err=ts_err, fp=ts_fp, eval=EvalSpec(cmd="npx tsc --noEmit"), id="cix_abcdef0123456789", **ts_fields)
    ledger.write_text(_line(plr) + "\n" + _line(ts) + "\n", encoding="utf-8")
    monkeypatch.setenv("CLAIMIDX_HOME", str(ledger))
    store = Store(tmp_path / "ix.sqlite")
    result = pull(store, url=str(ledger))
    assert result["skipped_n"] == 0
    assert result["imported"] == 2
    stored_plr = store.get(plr.id)
    stored_ts = store.get(ts.id)
    assert stored_plr is not None and stored_plr.fp == plr_fp
    assert stored_ts is not None and stored_ts.fp == ts_fp
    # Lookup by today's algorithm still finds the old rows via fingerprints() / fp_alias
    assert [c.id for c in store.by_fingerprints(fingerprints(err=plr_err, **plr_fields))] == [plr.id]
    assert [c.id for c in store.by_fingerprints(fingerprints(err=ts_err, **ts_fields))] == [ts.id]


def test_pull_rejects_short_fieldset_rows_with_reason():
    err = "TypeError: short field-set must not be scored"
    full = _claim(err=err, eco="py")
    raw = json.loads(full.model_dump_json())
    # Drop a required v1 field after declaring v=1
    short = dict(raw)
    short.pop("eval")
    claims, skipped = parse_ledger(json.dumps(short) + "\n" + json.dumps(raw) + "\n")
    assert len(claims) == 1
    assert claims[0].id == full.id
    assert skipped and "field-set v=1 missing eval" in skipped[0]
    # Declaring an unknown field-set version is also a skip, even if other keys look complete
    future = dict(raw)
    future["v"] = 99
    _, skipped_future = parse_ledger(json.dumps(future) + "\n")
    assert skipped_future and "unknown field-set version 99" in skipped_future[0]


def test_receipt_fieldset_rejects_short_observation(tmp_path: Path):
    from claimidx.fieldset import fieldset_rejection
    from claimidx.store import Store

    short = {"v": 2, "kind": "confirm", "payload": {}}  # missing actor
    assert "missing" in (fieldset_rejection(short) or "")
    store = Store(tmp_path / "ix.sqlite")
    out = store.import_protocol_events([short])
    assert out.get("skipped") == 1
    assert out.get("accepted", 0) == 0
    complete = {"v": 2, "kind": "confirm", "actor": "did:claimidx:agent-identity"}
    assert fieldset_rejection(complete) is None
