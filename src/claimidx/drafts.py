"""Local ingest drafts. Not claims. Not shared. Promote to ingest when ready."""

from __future__ import annotations

import json
import secrets
from typing import Any

from .fingerprint import classify, fingerprint, normalize_error
from .policy import PolicyError, inspect_claim
from .public import eval_is_proof, ingest_warnings
from .security import SecretError
from .session import session_id, utc_ts
from .store import Store


def _draft_id() -> str:
    return "draft_" + secrets.token_hex(8)


def stash_draft(
    store: Store,
    *,
    err: str,
    fix_k: str,
    fix_b: str,
    eval_cmd: str,
    eco: str = "other",
    rt: str = "",
    dep: list[str] | None = None,
    note: str = "",
    own: str = "",
) -> dict[str, Any]:
    dep = list(dep or [])
    warnings: list[str] = []
    try:
        inspect_claim(err=err, fix_k=fix_k, fix_b=fix_b or "pending", eval_cmd=eval_cmd or "true", note=note, own=own or "did:claimidx:draft")
    except (PolicyError, SecretError) as e:
        return {"ok": False, "error": str(e), "warnings": warnings}
    if not (fix_b or "").strip():
        warnings.append("fix.b empty; fill before promote")
    proof = eval_is_proof(eval_cmd or "")
    if not proof:
        warnings.append("eval_proof: false; discriminating eval required before share")
    warnings.extend(ingest_warnings(err, eval_cmd or "true"))
    cls = classify(err)
    fp = fingerprint(err=err, cls=cls, eco=eco or "", rt=rt or "", dep=dep)
    did = _draft_id()
    payload = {
        "id": did,
        "err": err,
        "cls": cls,
        "fp": fp,
        "fix_k": fix_k,
        "fix_b": fix_b,
        "eval": eval_cmd or "true",
        "eco": eco or "other",
        "rt": rt or "",
        "dep": dep,
        "note": note or "",
        "own": own or "",
        "eval_proof": proof,
        "ts": utc_ts(),
    }
    with store._conn() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, fp TEXT, json TEXT NOT NULL, ts TEXT NOT NULL
            )"""
        )
        con.execute(
            "INSERT OR REPLACE INTO drafts(id, fp, json, ts) VALUES (?,?,?,?)",
            (did, fp, json.dumps(payload, ensure_ascii=False), payload["ts"]),
        )
    store.session_record(session_id(), kind="draft", fp=fp, claim_id=did, detail={"eval_proof": proof})
    return {"ok": True, "draft_id": did, "fp": fp, "eval_proof": proof, "warnings": warnings, "err": normalize_error(err)}


def get_draft(store: Store, draft_id: str) -> dict[str, Any] | None:
    with store._conn() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS drafts (
                id TEXT PRIMARY KEY, fp TEXT, json TEXT NOT NULL, ts TEXT NOT NULL
            )"""
        )
        row = con.execute("SELECT json FROM drafts WHERE id=?", (draft_id,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["json"])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def promote_draft(store: Store, draft_id: str, *, own: str | None = None) -> dict[str, Any]:
    from .query import ingest

    data = get_draft(store, draft_id)
    if not data:
        return {"ok": False, "error": "missing draft"}
    out = ingest(
        str(data.get("err") or ""),
        fix_k=str(data.get("fix_k") or "constraint"),
        fix_b=str(data.get("fix_b") or ""),
        eval=str(data.get("eval") or "true"),
        eco=str(data.get("eco") or "other"),
        rt=str(data.get("rt") or ""),
        dep=list(data.get("dep") or []),
        note=str(data.get("note") or ""),
        own=own or str(data.get("own") or "") or None,
        db=store.path,
    )
    with store._conn() as con:
        con.execute("DELETE FROM drafts WHERE id=?", (draft_id,))
    out = dict(out)
    out["draft_id"] = draft_id
    out["ok"] = True
    return out
