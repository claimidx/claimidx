"""Prune: only claims whose eval can discriminate survive; seed counters are not evidence."""

from __future__ import annotations

import json
from pathlib import Path

from claimidx.fingerprint import fingerprint
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.prune import prune_ledger, prune_seed_rows, prune_store, upgrade_eval, worth_keeping
from claimidx.public import eval_is_proof
from claimidx.store import Store


def _claim(err: str, *, eco: str = "py", fix_k: str = "pin", fix_b: str = "x==1.0", ev: str = "true", own: str = "did:claimidx:t", cid: str = "") -> Claim:
    c = Claim(fp=fingerprint(err=err, eco=eco), cls="other", err=err, eco=eco, fix=Fix(k=fix_k, b=fix_b), eval=EvalSpec(cmd=ev), own=own)
    if cid:
        c.id = cid
    return c


def test_upgrade_eval_uses_what_the_claim_already_says():
    c = _claim("ModuleNotFoundError: No module named 'tomli'", fix_b="tomli==2.0.1")
    c.cls = "module_not_found"
    ev, how = upgrade_eval(c)
    assert how == "refined" and eval_is_proof(ev) and "tomli" in ev
    c = _claim("error[E0432]: unresolved import `serde`", eco="rust", fix_k="patch", fix_b="added serde")
    c.cls = "module_not_found"
    assert upgrade_eval(c) == ("cargo pkgid serde", "target")
    c = _claim("RuntimeError: something bespoke", fix_k="config", fix_b="set X=1")
    assert upgrade_eval(c)[1] == "hint" and not worth_keeping(c)
    c = _claim("RuntimeError: proven", fix_k="patch", fix_b="d", ev="python -m pytest -q")
    assert upgrade_eval(c) == ("python -m pytest -q", "kept")
    # A bare import is proof of presence: the failure only when the failure was a missing dependency.
    oom = _claim("RuntimeError: CUDA out of memory", fix_k="constraint", fix_b="Lower the batch size")
    oom.dep = ["torch@2.4.0"]
    assert upgrade_eval(oom)[1] == "hint"
    attr = _claim("AttributeError: module 'tomli' has no attribute 'load'", fix_k="pin", fix_b="tomli==2.0.1")
    assert upgrade_eval(attr)[1] == "refined"  # an exact pin's version check is about the pin
    # The same rule for evals that were already there: a bare import on a non-dependency failure is no proof.
    kept_wrong = _claim("RuntimeError: CUDA out of memory", fix_k="constraint", fix_b="Lower the batch size", ev='python -c "import torch"')
    assert upgrade_eval(kept_wrong)[1] == "hint"
    kept_right = _claim("ModuleNotFoundError: No module named 'torch'", fix_k="constraint", fix_b="torch", ev='python -c "import torch"')
    kept_right.cls = "module_not_found"
    assert upgrade_eval(kept_right) == ('python -c "import torch"', "kept")
    recipe = _claim("RuntimeError: CUDA out of memory", fix_k="patch", fix_b="diff --git a/x b/x", ev="python -m pytest -q")
    assert upgrade_eval(recipe)[1] == "kept"  # the author's own recipe
    # A discriminating eval the policy refuses to run is no proof either.
    c = _claim("ModuleNotFoundError: No module named 'subprocess'", fix_k="constraint", fix_b="subprocess")
    c.cls = "module_not_found"
    assert upgrade_eval(c)[1] == "hint"
    # A range pin's generated version check is too long for the public projection: fall back to the import.
    c = _claim("ModuleNotFoundError: No module named 'httpx'", fix_k="pin", fix_b="httpx>=0.27,<1")
    c.cls = "module_not_found"
    assert upgrade_eval(c) == ('python -c "import httpx"', "target")


def test_prune_store_retires_hints_and_upgrades_the_rest(tmp_path: Path):
    store = Store(str(tmp_path / "ix.sqlite"))
    keep = _claim("ModuleNotFoundError: No module named 'tomli'", fix_b="tomli==2.0.1")
    keep.cls = "module_not_found"
    drop = _claim("RuntimeError: bespoke thing", fix_k="config", fix_b="set X=1")
    proof = _claim("RuntimeError: proven", fix_k="patch", fix_b="d", ev="python -m pytest -q")
    for c in (keep, drop, proof):
        store.put(c)
    dry = prune_store(store, apply=False)
    assert (dry.seen, dry.kept, dry.upgraded, dry.dropped) == (3, 2, 1, 1)
    assert store.get(drop.id) is not None  # dry run touches nothing
    rep = prune_store(store, apply=True, actor="did:claimidx:t")
    assert rep.dropped_ids == [drop.id]
    assert store.get(drop.id) is None
    assert store.get(keep.id).eval.cmd != "true" and "tomli" in store.get(keep.id).eval.cmd
    assert store.get(proof.id).eval.cmd == "python -m pytest -q"
    kinds = {e.get("kind") for e in store.events(limit=50)}
    assert "prune" in kinds and "prune-upgrade" in kinds


def test_store_delete_removes_the_row_and_its_search_entry(tmp_path: Path):
    store = Store(str(tmp_path / "ix.sqlite"))
    c = _claim("ValueError: gone soon", fix_k="patch", fix_b="d", ev="python -m pytest -q")
    store.put(c)
    assert store.get(c.id) is not None
    assert store.delete(c.id, actor="did:claimidx:t", reason="test") is True
    assert store.get(c.id) is None and store.delete(c.id) is False
    assert not [x for x in store.all() if x.id == c.id]


def test_prune_ledger_moves_hint_rows_to_retired(tmp_path: Path):
    ledger = tmp_path / "claims.jsonl"
    retired = tmp_path / "claims-retired.jsonl"
    keep = _claim("ModuleNotFoundError: No module named 'tomli'", fix_b="tomli==2.0.1", cid="cix_00000000000000a1")
    keep.cls = "module_not_found"
    drop = _claim("RuntimeError: bespoke thing", fix_k="config", fix_b="set X=1", cid="cix_00000000000000d1")
    ledger.write_text("\n".join(json.dumps(json.loads(c.model_dump_json())) for c in (keep, drop)) + "\n", encoding="utf-8")
    retired.write_text(
        json.dumps(json.loads(_claim("KeyError: <STR>", fix_k="patch", fix_b="x", cid="cix_00000000000000e1").model_dump_json())) + "\n", encoding="utf-8"
    )
    rep = prune_ledger(ledger, retired, apply=False)
    assert (rep.seen, rep.kept, rep.dropped, rep.upgraded) == (2, 1, 1, 1)
    assert "cix_00000000000000d1" in ledger.read_text(encoding="utf-8")
    prune_ledger(ledger, retired, apply=True)
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["id"] for r in rows] == ["cix_00000000000000a1"] and "tomli" in rows[0]["eval"]["cmd"]
    gone = [json.loads(ln) for ln in retired.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert [r["id"] for r in gone] == ["cix_00000000000000e1", "cix_00000000000000d1"]
    assert "retired: eval is a hint" in gone[-1]["note"]
    # The retired row is unchanged otherwise: its fingerprint still recomputes.
    c = Claim.model_validate(gone[-1])
    assert c.fp == fingerprint(err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=c.dep)


def test_prune_seed_rows_drops_hints_and_forgets_invented_counters():
    rows = [
        {
            "id": "spr_0000000000000001",
            "err": "ModuleNotFoundError: No module named 'pydantic'",
            "eco": "py",
            "fix": ("constraint", "pydantic"),
            "eval": "true",
            "st": "confirmed",
            "nc": 11,
            "nf": 1,
        },
        {"id": "spr_0000000000000002", "err": "RuntimeError: folklore", "eco": "py", "fix": ("config", "set X"), "eval": "true", "st": "confirmed", "nc": 9},
        {
            "id": "spr_0000000000000003",
            "err": "TypeError: params is a Promise",
            "eco": "npm",
            "fix": ("patch", "await params"),
            "eval": "npx tsc --noEmit",
            "nc": 4,
        },
    ]
    out, rep = prune_seed_rows(rows)
    assert [r["id"] for r in out] == ["spr_0000000000000001", "spr_0000000000000003"] and rep.dropped_ids == ["spr_0000000000000002"]
    assert out[0]["eval"] == 'python -c "import pydantic"'
    assert all("nc" not in r and "nf" not in r and "st" not in r for r in out)


def test_bundled_seeds_all_carry_proof_and_no_invented_counters():
    from claimidx.seed_data import materialize

    seeds = materialize()
    assert seeds, "seeds are the first prior art a fresh install sees"
    for s in seeds:
        assert eval_is_proof(s.eval.cmd), (s.id, s.eval.cmd)
        assert s.nc == 0 and s.nf == 0 and s.st == "proposed", (s.id, s.nc, s.st)
