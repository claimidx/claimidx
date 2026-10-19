"""In-process ask/ingest/verify. Harnesses import this instead of shelling out to the CLI.

Never applies fix.b. Never auto-confirms. ingest() shares like the CLI unless share=False.
verify() dry_run defaults True: list claims, do not run evals/venv/pip.
"""

from __future__ import annotations

import os
import time
from typing import Any

from .fingerprint import classify, fingerprint, normalize_error
from .match import dead_end_claims, hit_compact, hit_row, rank, rank_near, similarity, verdict_for
from .models import Claim, EvalSpec, Fix
from .store import DEFAULT_DB, Store, force_reset_emits, force_reset_from
from .team import resolve_owner


def retrieve(store: Store, q: dict[str, Any], *, k: int = 5, actor: str | None = None, kind: str = "ask"):
    """Rank and log hit/n/ms. Does not store the raw err. Returns (hits, candidates)."""
    t0 = time.monotonic()
    candidates = store.candidates(
        fp=str(q.get("fp") or ""),
        err=str(q.get("err") or ""),
        cls=str(q.get("cls") or ""),
    )
    hits = rank(q, candidates, k=k)
    ms = int((time.monotonic() - t0) * 1000)
    store.log_ask(actor or resolve_owner(None), hits, ms=ms, q=q, kind=kind)
    return hits, candidates


def gate_explain(
    store: Store | None,
    q: dict[str, Any],
    candidates: list[Claim],
    *,
    cwd: str | None = None,
) -> list[dict[str, Any]]:
    """Say which payload token kept a neighbor off the rank. Does not change rank or similarity."""
    from .binding import digest_drift
    from .match import _ERR_BOILER, _err_tokens

    qerr = str(q.get("err") or "")
    qcls = str(q.get("cls") or classify(qerr))
    qdep = list(q.get("dep") or [])
    qrt = str(q.get("rt") or "")
    qeco = str(q.get("eco") or "")
    q_payload = _err_tokens(qerr) - _ERR_BOILER
    rows: list[dict[str, Any]] = []
    for claim in candidates:
        sim = float(similarity(q, claim) or 0.0)
        if sim <= 0 and (claim.cls or "") != qcls:
            continue
        c_payload = _err_tokens(claim.err or "") - _ERR_BOILER
        row: dict[str, Any] = {
            "id": claim.id,
            "sim": sim,
            "query_only": sorted(q_payload - c_payload)[:12],
            "claim_only": sorted(c_payload - q_payload)[:12],
        }
        if not qdep and claim.dep:
            if fingerprint(err=qerr, cls=qcls or classify(qerr), eco=qeco, rt=qrt, dep=list(claim.dep)) == claim.fp:
                row["family_dep"] = list(claim.dep)
        if cwd and store is not None:
            proof = store.proof_for(claim.id)
            if proof is not None and getattr(proof, "observed_digest", None):
                drift = digest_drift(proof.observed_digest, cwd, claim.eco or qeco)
                if drift:
                    row["digest_mismatch"] = drift
        rows.append(row)
    rows.sort(key=lambda r: (-float(r["sim"]), str(r["id"])))
    return rows[:8]


def format_gate_line(row: dict[str, Any]) -> str:
    """One miss line: id, the tokens on each side, then family_dep and digest_mismatch when set."""
    query_only = ",".join(row.get("query_only") or [])
    claim_only = ",".join(row.get("claim_only") or [])
    line = f"{row.get('id') or ''} query {query_only} claim {claim_only}"
    if row.get("family_dep"):
        line += " family_dep " + ",".join(str(d) for d in row["family_dep"])
    if row.get("digest_mismatch"):
        line += " digest_mismatch " + ",".join(str(d) for d in row["digest_mismatch"])
    return line


def miss_enrichment(
    store: Store,
    q: dict[str, Any],
    candidates: list[Claim],
    *,
    k: int = 5,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Near neighbors + contested/wontfix family rows. Never promoted into claims[]."""
    near_hits = rank_near(q, candidates, k=min(3, k))
    pool: list[Claim] = []
    seen: set[str] = set()
    for c in list(candidates) + (store.by_fp(str(q.get("fp") or "")) if q.get("fp") else []):
        if c.id in seen:
            continue
        seen.add(c.id)
        pool.append(c)
    ends = dead_end_claims(q, pool, k=min(5, k))
    near_why: list[str] = []
    if near_hits:
        near_why.append("below_threshold")
    else:
        near_why.append("no_near_neighbors")
    if ends:
        near_why.append("dead_end")
    out: dict[str, Any] = {
        "near": [hit_compact(q, c, s) for c, s in near_hits],
        "near_why": near_why,
        "dead_ends": [hit_compact(q, c, float(similarity(q, c) or 0.0)) for c in ends],
    }
    gate = gate_explain(store, q, pool, cwd=cwd)
    if gate:
        out["gate"] = gate
    return out


def ask(
    err: str,
    *,
    eco: str = "",
    rt: str = "",
    dep: list[str] | None = None,
    k: int = 5,
    db: str | os.PathLike[str] | None = None,
    cwd: str | None = None,
) -> dict:
    """Query the local index. Same payload as `claimidx --fmt json ask`."""
    from .impact import path_b_cta

    path = db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB)
    store = Store(path)
    dep = dep or []
    cls = classify(err)
    q: dict[str, Any] = {"err": err, "cls": cls, "eco": eco or "", "rt": rt or "", "dep": dep}
    q["fp"] = fingerprint(err=err, cls=cls, eco=eco or "", rt=rt or "", dep=dep)
    actor = resolve_owner(None)
    hits, candidates = retrieve(store, q, k=k, actor=actor)
    session = store.session_summary(fp=q["fp"])
    path_b = path_b_cta(store, actor)
    if not hits:
        miss = {
            "verdict": verdict_for(q, []),
            "hit": False,
            "fp": q["fp"],
            "cls": cls,
            "err": normalize_error(err),
            "n": 0,
            "claims": [],
            "session": session,
            "path_b": path_b,
        }
        miss.update(miss_enrichment(store, q, candidates, k=k, cwd=cwd))
        return miss
    return {
        "verdict": verdict_for(q, hits),
        "hit": True,
        "fp": q["fp"],
        "cls": cls,
        "err": normalize_error(err),
        "n": len(hits),
        "claims": [hit_row(q, c, s) for c, s in hits],
        "session": session,
        "path_b": path_b,
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
    alternative: bool = False,
    share: bool | None = None,
    share_yes: bool = False,
    expect: int = 0,
    db: str | os.PathLike[str] | None = None,
    cwd: str | None = None,
    observe_digest: bool = False,
) -> dict[str, Any]:
    """Write a claim to the local index and share it the way the CLI does.

    Private home may still receive the full record when configured. The public commons needs
    `share_yes=True` (explicit confirm). `share=False` is `--local`: the claim stays on this
    machine until `claimidx share <id> --yes`. `CLAIMIDX_SHARE=0` / `CLAIMIDX_COMMONS=0` keep everything
    local. A hint eval never leaves the machine anyway.
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
    if existing and not force and not alternative:
        c = existing[0]
        return {"exists": True, "id": c.id, "st": c.st, "fp": c.fp}
    extra: dict[str, Any] = {}
    reset: dict[str, int | str] = {}
    if existing and force:
        extra["id"] = existing[0].id
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
    bound = store.bind_after_publish(claim, cwd=cwd, observe_digest=observe_digest)
    if existing and alternative:
        from .graph import Relation

        new_graph = store.graph(claim.id)
        old_graph = store.graph(existing[0].id)
        if new_graph and old_graph:
            store.add_relation(
                Relation(
                    source_id=new_graph["remedy"]["id"],
                    target_id=old_graph["remedy"]["id"],
                    kind="alternative",
                    actor=claim.own,
                )
            )
    from .public import eval_is_proof, ingest_warnings

    out: dict[str, Any] = {
        "exists": False,
        "id": claim.id,
        "st": claim.st,
        "fp": claim.fp,
        "own": claim.own,
        "nr": claim.nr,
        "eval_proof": eval_is_proof(claim.eval.cmd),
        **bound,
    }
    warns = ingest_warnings(err, claim.eval.cmd, cls=claim.cls, dep=claim.dep, eco=claim.eco)
    if warns:
        out["warn"] = "; ".join(warns)
    if force_reset_emits(reset):
        out["force_reset"] = reset
    if share is False:
        # A durable decision, not a switch for this run: bulk shares and later replays skip it.
        from .home import local_status, mark_local

        mark_local(store, claim.id, claim.own)
        out["share"] = local_status(claim.id)
    else:
        from .home import maybe_share

        shared = maybe_share(store, claim, commons_yes=bool(share_yes or share is True))
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
