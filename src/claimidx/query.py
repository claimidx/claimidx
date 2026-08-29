"""In-process ask. Harnesses import this instead of shelling out to the CLI.

Never applies fix.b. Never auto-confirms. A hit is evidence.
"""
from __future__ import annotations

import os

from .fingerprint import classify, fingerprint, normalize_error
from .match import hit_row, rank
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
