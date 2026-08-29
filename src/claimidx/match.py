from __future__ import annotations

from .fingerprint import classify, fingerprint, normalize_error
from .models import Claim


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(x.lower() for x in a), set(x.lower() for x in b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _dep_names(items: list[str] | None) -> set[str]:
    """Package names only. next@15.0 and next@15.2 are the same prior-art env."""
    out: set[str] = set()
    for raw in items or []:
        s = raw.strip().lower()
        if not s:
            continue
        if s.startswith("@") and s.count("@") >= 2:
            s = s.rsplit("@", 1)[0]
        elif "@" in s:
            s = s.split("@", 1)[0]
        if s:
            out.add(s)
    return out


_ERR_BOILER = {
    "modulenotfounderror", "importerror", "error", "no", "module", "named",
    "cannot", "find", "or", "its", "corresponding", "type", "declarations",
    "the", "a", "an", "from", "is", "not", "defined", "import", "name",
}


def _err_sim(a: str, b: str) -> float:
    ta, tb = set(normalize_error(a).lower().split()), set(normalize_error(b).lower().split())
    if not ta or not tb:
        return 0.0
    da, db = ta - _ERR_BOILER, tb - _ERR_BOILER
    if da or db:
        if not da or not db:
            return 0.0
        return len(da & db) / len(da | db)
    return len(ta & tb) / len(ta | tb)


# Class + eco without error overlap was enough to rank unrelated seed claims
# (PermissionError / ModuleNotFoundError hits on Gradle PATH, MCP own, …).
_ERR_FLOOR = 0.35


def similarity(query: Claim | dict, cand: Claim) -> float:
    if isinstance(query, Claim):
        qfp, qcls, qerr, qeco, qdep = query.fp, query.cls, query.err, query.eco, query.dep
    else:
        qerr = query.get("err") or ""
        qcls = query.get("cls") or classify(qerr)
        qeco = query.get("eco") or ""
        qdep = query.get("dep") or []
        qfp = query.get("fp") or fingerprint(err=qerr, cls=qcls, eco=qeco, rt=query.get("rt") or "", dep=qdep)
    if qfp == cand.fp:
        return 1.0
    err = _err_sim(qerr, cand.err)
    if err < _ERR_FLOOR:
        return 0.0
    qn, cn = _dep_names(qdep), _dep_names(cand.dep)
    if qn and cn and qn.isdisjoint(cn):
        return 0.0
    s = 0.0
    s += 0.45 * err
    s += 0.20 * (1.0 if qcls and qcls == cand.cls else 0.0)
    s += 0.15 * (1.0 if qeco and qeco == cand.eco else 0.0)
    s += 0.20 * _jaccard(qdep, cand.dep)
    return s


def rank(query: Claim | dict, claims: list[Claim], *, k: int = 5, min_sim: float = 0.28) -> list[tuple[Claim, float]]:
    scored: list[tuple[Claim, float]] = []
    for c in claims:
        sim = similarity(query, c)
        if sim < min_sim:
            continue
        scored.append((c, sim * (0.5 + 0.5 * c.score())))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
