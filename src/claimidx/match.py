from __future__ import annotations

import re
from datetime import UTC, datetime

from .fingerprint import (
    classify,
    fingerprint,
    normalization_risk,
    normalize_error,
    runtime_proof_key,
)
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


def hold_applies(query_rt: str | None, claim_rt: str | None) -> bool:
    """Stored nr counts for this consumer only when proof-grain rt matches.

    No same-major fallback: py@3.12 does not prove py@3.9. A keyed claim
    against an omitted query rt is unproven here. Both empty still applies
    (non-python/node heads, or unkeyed legacy rows).
    """
    if rt_drift(query_rt, claim_rt):
        return False
    if (claim_rt or "").strip() and not (query_rt or "").strip():
        return False
    return True


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
        ts = ts.replace(tzinfo=UTC)
    when = now or datetime.now(UTC)
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
    qerr = query.err if isinstance(query, Claim) else (query.get("err") or "")
    if eval_is_proof(claim.eval.cmd):
        # Stored err is already canonical. Same normalize_error form is not
        # exact: quoted non-module tokens collapse to <STR>.
        if (qerr or "").strip() != (claim.err or ""):
            warns.append("eval_proof is recipe-per-fp, not query-err match")
    else:
        warns.append("eval is not proof")
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
    stored_nr = int(getattr(claim, "nr", 0) or 0)
    if stored_nr and not hold_applies(qrt, crt):
        warns.append(f"nr={stored_nr} for {crt or 'unkeyed'}; unproven here")
    if int(claim.nc or 0) >= 1 and stored_nr == 0:
        warns.append("nc without replay")
    return warns


def _query_fp(query: Claim | dict) -> str:
    if isinstance(query, Claim):
        return query.fp
    fp = str(query.get("fp") or "")
    if fp:
        return fp
    qerr = query.get("err") or ""
    qcls = query.get("cls") or classify(qerr)
    qeco = query.get("eco") or ""
    qrt = query.get("rt") or ""
    qdep = query.get("dep") or []
    return fingerprint(err=qerr, cls=qcls, eco=qeco, rt=qrt, dep=qdep)


def match_tokens(query_err: str, claim_err: str) -> list[str]:
    da = _err_tokens(query_err) - _ERR_BOILER
    db = _err_tokens(claim_err) - _ERR_BOILER
    return sorted(da & db)[:12]


def untrusted(query: Claim | dict, claim: Claim, *, nr: int) -> list[str]:
    codes: list[str] = []
    src = getattr(claim, "src", "local") or "local"
    if src == "home":
        codes.append("src=home")
    elif src == "seed":
        codes.append("src=seed")
    if claim.st == "contested":
        codes.append("st=contested")
    if not eval_is_proof(claim.eval.cmd):
        codes.append("eval_hint")
    qdep = query.dep if isinstance(query, Claim) else (query.get("dep") or [])
    if dep_drift(qdep, claim.dep):
        codes.append("dep_drift")
    qrt = (query.rt if isinstance(query, Claim) else (query.get("rt") or "")).strip()
    if rt_drift(qrt, claim.rt):
        codes.append("rt_drift")
    if int(claim.nc or 0) >= 1 and nr == 0:
        codes.append("nc without replay")
    qerr = query.err if isinstance(query, Claim) else (query.get("err") or "")
    if normalization_risk(qerr):
        codes.append("normalization_risk")
    return codes


def disposition_for(query: Claim | dict, claim: Claim, ann: dict) -> dict:
    """Machine-readable next action. Advice only — never execute fix.b from this."""
    why: list[str] = []
    untrusted_codes = list(ann.get("untrusted") or [])
    tokens = list(ann.get("tokens") or [])
    evidence = ann.get("evidence") or "retrieved"
    match = ann.get("match") or "similar"
    fix_k = getattr(getattr(claim, "fix", None), "k", "") or ""
    cid = claim.id

    if fix_k == "wontfix":
        why.append("fix.k=wontfix")
        return {
            "action": "skip",
            "why": why,
            "suggested": [f"claimidx alternatives {claim.fp}", "record an alternative remedy if a real fix exists"],
        }
    if claim.st == "contested" or "st=contested" in untrusted_codes:
        why.append("st=contested")
        if int(claim.nf or 0) >= 1:
            why.append(f"nf={claim.nf}")
        return {
            "action": "fail_or_alternative",
            "why": why,
            "suggested": [
                f"claimidx alternatives {claim.fp}",
                f"claimidx fail {cid}",
                "claimidx ingest … --alternative",
            ],
        }
    if int(claim.nf or 0) >= 1:
        why.append(f"nf={claim.nf}")
        return {
            "action": "fail_or_alternative",
            "why": why,
            "suggested": [f"claimidx confirm --replay {cid}", f"claimidx fail {cid}"],
        }

    replay_codes = {
        "src=home",
        "src=seed",
        "dep_drift",
        "rt_drift",
        "eval_hint",
        "nc without replay",
        "normalization_risk",
    }
    for code in untrusted_codes:
        if code in replay_codes or code.startswith("dep_drift") or code.startswith("rt_drift"):
            why.append(code)
    if why:
        return {
            "action": "replay_before_apply",
            "why": why,
            "suggested": [f"claimidx confirm --replay {cid}", "do not apply fix.b until replay holds"],
        }

    if match == "similar" and len(tokens) < 1:
        why.append("match=similar")
        why.append("token_overlap=0")
        return {
            "action": "reason_only",
            "why": why,
            "suggested": ["compare err tokens before applying", f"claimidx explain {cid}"],
        }

    if evidence == "reproduced" and not untrusted_codes:
        why.append("evidence=reproduced")
        return {
            "action": "apply_with_caution",
            "why": why,
            "suggested": [f"reason over fix.b then apply; claimidx explain {cid}"],
        }

    if match == "similar":
        why.append("match=similar")
        why.append(f"token_overlap={len(tokens)}")
        return {
            "action": "reason_only",
            "why": why,
            "suggested": [f"claimidx explain {cid}", f"claimidx confirm --replay {cid}"],
        }

    why.append("evidence=retrieved")
    return {
        "action": "replay_before_apply",
        "why": why,
        "suggested": [f"claimidx confirm --replay {cid}"],
    }


def annotate(query: Claim | dict, claim: Claim, sim: float) -> dict:
    qdep = query.dep if isinstance(query, Claim) else (query.get("dep") or [])
    qrt = (query.rt if isinstance(query, Claim) else (query.get("rt") or "")).strip()
    qerr = query.err if isinstance(query, Claim) else (query.get("err") or "")
    stored_nr = int(getattr(claim, "nr", 0) or 0)
    nr = stored_nr if hold_applies(qrt, claim.rt) else 0
    exact = _query_fp(query) == claim.fp
    ann = {
        "sim": round(sim, 4),
        "score": round(claim.score(), 4),
        "age_days": round(age_days(claim), 1),
        "dep_drift": dep_drift(qdep, claim.dep),
        "rt_drift": rt_drift(qrt, claim.rt) or {},
        "eval_proof": eval_is_proof(claim.eval.cmd),
        "nr": nr,
        "warn": hit_warn(query, claim),
        "evidence": "reproduced" if nr > 0 else "retrieved",
        "match": "exact" if exact else "similar",
        "tokens": match_tokens(qerr, claim.err),
        "untrusted": untrusted(query, claim, nr=nr),
    }
    ann["disposition"] = disposition_for(query, claim, ann)
    return ann


_ERR_BOILER = {
    "modulenotfounderror",
    "importerror",
    "error",
    "no",
    "module",
    "named",
    "cannot",
    "find",
    "or",
    "its",
    "corresponding",
    "type",
    "declarations",
    "the",
    "a",
    "an",
    "from",
    "is",
    "not",
    "defined",
    "import",
    "name",
    # schema-break skeleton (pydantic/zod). Payload is field/literal/type-tag.
    "be",
    "for",
    "input",
    "should",
    "valid",
    "validation",
    "validationerror",
    "pydantic",
    "pydantic.validationerror",
    "input_value",
    "input_type",
    "value",
    "given",
    "got",
    "expected",
    "received",
    "required",
    "field",
    "string",
    "n",
    "str",
    "url",
    "path",
    "hex",
}
_ERR_SPLIT = re.compile(r"[^a-z0-9_@./+-]+")


def _err_tokens(raw: str) -> set[str]:
    return {p for p in _ERR_SPLIT.split(normalize_error(raw).lower()) if p}


def _err_sim(a: str, b: str) -> float:
    ta, tb = _err_tokens(a), _err_tokens(b)
    if not ta or not tb:
        return 0.0
    da, db = ta - _ERR_BOILER, tb - _ERR_BOILER
    if da or db:
        if not da or not db:
            return 0.0
        inter = da & db
        if not inter:
            return 0.0
        # A short payload ("Input should be thumbs_up") is a subset of a full
        # dump; Jaccard against the dump falls under the floor. Skeleton
        # overlap without that payload is not a subset and stays Jaccard.
        if da <= db or db <= da:
            return 1.0
        return len(inter) / len(da | db)
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


NEAR_FLOOR = 0.12
HIT_MIN_SIM = 0.28


def rank_near(
    query: Claim | dict,
    claims: list[Claim],
    *,
    k: int = 3,
    floor: float = NEAR_FLOOR,
    ceiling: float = HIT_MIN_SIM,
) -> list[tuple[Claim, float]]:
    """Below-hit-threshold cousins. Never promoted into claims[]."""
    scored: list[tuple[Claim, float]] = []
    for c in claims:
        sim = similarity(query, c)
        if sim < floor or sim >= ceiling:
            continue
        scored.append((c, sim))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]


def dead_end_claims(query: Claim | dict, claims: list[Claim], *, k: int = 5) -> list[Claim]:
    """Contested / wontfix rows in the same err family — useful on miss."""
    qerr = normalize_error((query.err if isinstance(query, Claim) else (query.get("err") or "")) or "")
    qeco = (query.eco if isinstance(query, Claim) else (query.get("eco") or "")).strip()
    qfp = _query_fp(query)
    out: list[Claim] = []
    seen: set[str] = set()
    for c in claims:
        if c.id in seen:
            continue
        if c.st != "contested" and getattr(c.fix, "k", "") != "wontfix":
            continue
        same_fp = c.fp == qfp
        same_err = (c.err or "") == qerr
        token_hit = bool(match_tokens(qerr, c.err or ""))
        eco_ok = not qeco or not c.eco or c.eco == qeco
        if same_fp or (eco_ok and (same_err or token_hit)):
            seen.add(c.id)
            out.append(c)
        if len(out) >= k:
            break
    return out


def hit_row(query: Claim | dict, claim: Claim, sim: float) -> dict:
    """Ask payload: annotation first so sim/score/warn are not buried."""
    row = annotate(query, claim, sim)
    row.update(claim.model_dump(mode="json"))
    return row


def hit_compact(query: Claim | dict, claim: Claim, sim: float) -> dict:
    """MCP / home-ask: enough to apply or skip, plus freshness."""
    row = annotate(query, claim, sim)
    row.update(
        {
            "id": claim.id,
            "st": claim.st,
            "src": getattr(claim, "src", "local"),
            "nc": claim.nc,
            "nf": claim.nf,
            "err": claim.err,
            "dep": claim.dep,
            "fix": claim.fix.model_dump(),
            "eval": claim.eval.model_dump(),
        }
    )
    return row
