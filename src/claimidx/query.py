"""In-process ask/ingest. Harnesses import this instead of shelling out to the CLI.

Never applies fix.b. Never auto-confirms. ingest() is local unless share=True.
"""
from __future__ import annotations

import os
from typing import Any

from .fingerprint import classify, fingerprint, normalize_error
from .match import hit_row, rank
from .models import Claim, EvalSpec, Fix
from .store import DEFAULT_DB, Store
from .team import resolve_owner


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
    q = {"err": err, "cls": cls, "eco": eco or "", "rt": rt or "", "dep": dep}
    q["fp"] = fingerprint(err=err, cls=cls, eco=q["eco"], rt=q["rt"], dep=dep)
    hits = rank(q, store.all(), k=k)
    store.log("ask", resolve_owner(None), hits[0][0].id if hits else "")
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
    db: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Write a claim to the local index. Does not share unless share=True.

    Formalization is ingest. Public/org share is a later opt-in.
    """
    path = db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB)
    store = Store(path)
    dep = dep or []
    cls = classify(err)
    fp = fingerprint(err=err, cls=cls, eco=eco or "", rt=rt or "", dep=dep)
    existing = store.by_fp(fp)
    if existing and not force:
        c = existing[0]
        return {"exists": True, "id": c.id, "st": c.st, "fp": c.fp}
    extra: dict[str, Any] = {}
    if existing:
        extra["id"] = existing[0].id
    claim = Claim(
        fp=fp,
        cls=cls,
        err=normalize_error(err),
        eco=eco or "other",
        rt=rt or "",
        dep=dep,
        tried=tried or [],
        fix=Fix(k=fix_k, b=fix_b),  # type: ignore[arg-type]
        eval=EvalSpec(cmd=eval),
        own=resolve_owner(own),
        note=note or "",
        **extra,
    )
    store.put(claim)
    store.log("publish", claim.own, claim.id)
    out: dict[str, Any] = {"exists": False, "id": claim.id, "st": claim.st, "fp": claim.fp, "own": claim.own}
    if share:
        from .home import maybe_share

        shared = maybe_share(store, claim)
        if shared:
            out["share"] = shared
    return out
