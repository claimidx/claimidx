from __future__ import annotations

from datetime import datetime, timezone

from .fingerprint import classify, fingerprint, normalize_error, normalization_risk, runtime_proof_key
from .models import Claim
from .public import eval_is_proof


def _jaccard(a: list[str], b: list[str]) -> float:
    sa, sb = set(x.lower() for x in a), set(x.lower() for x in b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _split_dep(raw: str) -> tuple[str, str]:
    """('next', '15.0.0') or ('@types/node', '20'). Empty version if unpinned."""
    s = (raw or "").strip()
    if not s:
        return "", ""
    if s.startswith("@") and s.count("@") >= 2:
        name, _, ver = s.rpartition("@")
        return name.lower(), ver
    if s.startswith("@"):
        return s.lower(), ""
    if "@" in s:
        name, _, ver = s.partition("@")
        return name.lower(), ver
    return s.lower(), ""


def _dep_names(items: list[str] | None) -> set[str]:
    """Package names only. next@15.0 and next@15.2 are the same prior-art env."""
    return {n for n, _ in (_split_dep(x) for x in (items or [])) if n}


def rt_drift(query_rt: str | None, claim_rt: str | None) -> dict[str, str] | None:
    """Different proof-grain runtimes. Empty if either side omitted."""
    qk = runtime_proof_key(query_rt or "")
    ck = runtime_proof_key(claim_rt or "")
    if qk and ck and qk != ck:
        return {"query": (query_rt or "").strip(), "claim": (claim_rt or "").strip()}
    return None


def dep_drift(query_dep: list[str] | None, claim_dep: list[str] | None) -> list[dict[str, str]]:
    """Same package name, different version string. Empty if either side is unpinned."""
    qmap = {n: v for n, v in (_split_dep(x) for x in (query_dep or [])) if n}
    cmap = {n: v for n, v in (_split_dep(x) for x in (claim_dep or [])) if n}
    out: list[dict[str, str]] = []
    for name, qv in qmap.items():
        cv = cmap.get(name)
        if qv and cv and qv != cv:
            out.append({"name": name, "query": qv, "claim": cv})
    return out


def age_days(claim: Claim, *, now: datetime | None = None) -> float:
    ts = claim.ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    when = now or datetime.now(timezone.utc)
    return max(0.0, (when - ts).total_seconds() / 86400)


def hit_warn(query: Claim | dict, claim: Claim) -> list[str]:
    """Machine-readable caution. Provenance is on the claim (`src`, `tried`, `eval`, `ts`)."""
    qdep = query.dep if isinstance(query, Claim) else (query.get("dep") or [])
    warns: list[str] = []
    age = age_days(claim)
    if age >= 45:
        warns.append(f"age {int(age)}d")
    if claim.st == "stale":
        warns.append("st=stale")
    elif claim.st == "contested":
        warns.append("st=contested; fail is the contradiction")
    if claim.nf >= 1:
        warns.append(f"nf={claim.nf}; replay in this env or fail")
    src = getattr(claim, "src", "local") or "local"
    if src == "seed":
        warns.append("src=seed; replay before apply")
    elif src == "home":
        warns.append("src=home; confirm requires replay")
    for d in dep_drift(qdep, claim.dep):
        warns.append(f"{d['name']} query={d['query']} claim={d['claim']}")
    if not eval_is_proof(claim.eval.cmd):
        warns.append("eval is not proof")
    qerr = query.err if isinstance(query, Claim) else (query.get("err") or "")
    risks = normalization_risk(qerr)
    if risks:
        warns.append("normalization_risk " + ",".join(risks) + "; replay")
    qrt = (query.rt if isinstance(query, Claim) else (query.get("rt") or "")).strip()
    crt = (claim.rt or "").strip()
    if crt and not qrt:
        warns.append("rt omitted; replay")
    drifted = rt_drift(qrt, crt)
    if drifted:
        warns.append(f"rt query={drifted['query']} claim={drifted['claim']}")
    if int(claim.nc or 0) >= 1 and int(getattr(claim, "nr", 0) or 0) == 0:
        warns.append("nc without replay")
    return warns


def annotate(query: Claim | dict, claim: Claim, sim: float) -> dict:
    qdep = query.dep if isinstance(query, Claim) else (query.get("dep") or [])
    qrt = (query.rt if isinstance(query, Claim) else (query.get("rt") or "")).strip()
    return {
        "sim": round(sim, 4),
        "score": round(claim.score(), 4),
        "age_days": round(age_days(claim), 1),
        "dep_drift": dep_drift(qdep, claim.dep),
        "rt_drift": rt_drift(qrt, claim.rt) or {},
        "eval_proof": eval_is_proof(claim.eval.cmd),
        "nr": int(getattr(claim, "nr", 0) or 0),
        "warn": hit_warn(query, claim),
    }


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
        qfp, qcls, qerr, qeco, qdep, qrt = query.fp, query.cls, query.err, query.eco, query.dep, query.rt
    else:
        qerr = query.get("err") or ""
        qcls = query.get("cls") or classify(qerr)
        qeco = query.get("eco") or ""
        qdep = query.get("dep") or []
        qrt = query.get("rt") or ""
        qfp = query.get("fp") or fingerprint(err=qerr, cls=qcls, eco=qeco, rt=qrt, dep=qdep)
    if qfp == cand.fp:
        s = 1.0
    else:
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
        # Same package, different pin: still a hit (agent sees dep_drift), ranked lower.
        if dep_drift(qdep, cand.dep):
            s *= 0.82
    # Fingerprint keeps Python major only; proof grain is minor. Still a hit.
    if rt_drift(qrt, cand.rt):
        s *= 0.82
    return s


def rank(query: Claim | dict, claims: list[Claim], *, k: int = 5, min_sim: float = 0.28) -> list[tuple[Claim, float]]:
    scored: list[tuple[Claim, float]] = []
    for c in claims:
        sim = similarity(query, c)
        if sim < min_sim:
            continue
        # Proof-weighted retrieval: replayable recipes surface first. Hints still hit.
        if eval_is_proof(c.eval.cmd):
            sim = min(1.0, sim * 1.08)
        else:
            sim *= 0.92
        scored.append((c, sim * (0.5 + 0.5 * c.score())))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def hit_row(query: Claim | dict, claim: Claim, sim: float) -> dict:
    """Ask payload: annotation first so sim/score/warn are not buried."""
    row = annotate(query, claim, sim)
    row.update(claim.model_dump(mode="json"))
    return row


def hit_compact(query: Claim | dict, claim: Claim, sim: float) -> dict:
    """MCP / home-ask: enough to apply or skip, plus freshness."""
    row = annotate(query, claim, sim)
    row.update({
        "id": claim.id,
        "st": claim.st,
        "src": getattr(claim, "src", "local"),
        "nc": claim.nc,
        "nf": claim.nf,
        "err": claim.err,
        "dep": claim.dep,
        "fix": claim.fix.model_dump(),
        "eval": claim.eval.model_dump(),
    })
    return row
