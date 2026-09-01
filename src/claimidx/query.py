"""In-process ask/ingest/verify. Harnesses import this instead of shelling out to the CLI.

Never applies fix.b. Never auto-confirms. ingest() is local unless share=True.
verify() dry_run defaults True: list claims, do not run evals/venv/pip.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .fingerprint import classify, fingerprint, normalize_error
from .match import hit_row, rank
from .models import Claim, EvalSpec, Fix
from .store import DEFAULT_DB, Store, force_reset_emits, force_reset_from
from .team import resolve_owner


def retrieve(store: Store, q: dict[str, Any], *, k: int = 5, actor: str | None = None, kind: str = "ask"):
    """Rank and log hit/n/ms. Does not store the raw err."""
    t0 = time.monotonic()
    hits = rank(q, store.all(), k=k)
    ms = int((time.monotonic() - t0) * 1000)
    store.log_ask(actor or resolve_owner(None), hits, ms=ms, q=q, kind=kind)
    return hits


def ask(
    err: str,
    *,
    eco: str = "",
    rt: str = "",
    dep: list[str] | None = None,
    k: int = 5,
    db: str | os.PathLike[str] | None = None,
) -> dict:
    """Query the local index. Same payload as `claimidx --fmt json ask`."""
    path = db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB)
    store = Store(path)
    dep = dep or []
    cls = classify(err)
    q: dict[str, Any] = {"err": err, "cls": cls, "eco": eco or "", "rt": rt or "", "dep": dep}
    q["fp"] = fingerprint(err=err, cls=cls, eco=eco or "", rt=rt or "", dep=dep)
    hits = retrieve(store, q, k=k)
    if not hits:
        return {"hit": False, "fp": q["fp"], "cls": cls, "err": normalize_error(err), "n": 0, "claims": []}
    return {
        "hit": True,
        "fp": q["fp"],
        "cls": cls,
        "err": normalize_error(err),
        "n": len(hits),
        "claims": [hit_row(q, c, s) for c, s in hits],
    }


def ingest(
    err: str,
    *,
    fix_k: str,
    fix_b: str,
    eval: str,
    eco: str = "other",
    rt: str = "",
    dep: list[str] | None = None,
    tried: list[str] | None = None,
    own: str | None = None,
    note: str = "",
    force: bool = False,
    share: bool = False,
    expect: int = 0,
    db: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Write a claim to the local index. Does not share unless share=True.

    Formalization is ingest. Public/org share is a later opt-in.
    """
    path = db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB)
    store = Store(path)
    dep = dep or []
    from .policy import inspect_claim

    own_did = resolve_owner(own)
    inspect_claim(err=err, fix_k=fix_k, fix_b=fix_b, eval_cmd=eval, note=note, own=own_did)
    from .public import refine_eval

    eval = refine_eval(eval, fix_k=fix_k, fix_b=fix_b, dep=dep, eco=eco)
    if force:
        cls, fp, existing = store.match_amend(
            err=err,
            cls=None,
            eco=eco or "",
            rt=rt or "",
            dep=dep,
        )
    else:
        cls = classify(err)
        fp = fingerprint(err=err, cls=cls, eco=eco or "", rt=rt or "", dep=dep)
        existing = store.by_fp(fp)
    if existing and not force:
        c = existing[0]
        return {"exists": True, "id": c.id, "st": c.st, "fp": c.fp}
    extra: dict[str, Any] = {}
    reset: dict[str, int | str] = {}
    if existing:
        extra["id"] = existing[0].id
        if force:
            reset = force_reset_from(existing[0])
    claim = Claim(
        fp=fp,
        cls=cls,
        err=normalize_error(err),
        eco=eco or "other",
        rt=rt or "",
        dep=dep,
        tried=tried or [],
        fix=Fix(k=fix_k, b=fix_b),  # type: ignore[arg-type]
        eval=EvalSpec(cmd=eval, expect=int(expect or 0)),
        own=resolve_owner(own),
        note=note or "",
        **extra,
    )
    store.publish(claim, claim.own, reset)
    from .public import eval_is_proof, ingest_warnings

    out: dict[str, Any] = {
        "exists": False,
        "id": claim.id,
        "st": claim.st,
        "fp": claim.fp,
        "own": claim.own,
        "nr": claim.nr,
        "eval_proof": eval_is_proof(claim.eval.cmd),
    }
    warns = ingest_warnings(err, claim.eval.cmd)
    if warns:
        out["warn"] = "; ".join(warns)
    if force_reset_emits(reset):
        out["force_reset"] = reset
    if share:
        from .home import maybe_share

        shared = maybe_share(store, claim)
        if shared:
            out["share"] = shared
    return out


def verify(
    *,
    k: int = 8,
    ids: list[str] | None = None,
    dry_run: bool = True,
    runnable: bool = False,
    harness: bool = False,
    own: str | None = None,
    db: str | os.PathLike[str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Batch replay. dry_run defaults True: list claims; do not run evals, venv, or pip."""
    from .replay import run

    path = db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB)
    store = Store(path)
    return run(
        store,
        k=k,
        ids=ids,
        own=own,
        dry_run=dry_run,
        runnable=runnable,
        harness_mode=harness,
        cwd=cwd,
    )
