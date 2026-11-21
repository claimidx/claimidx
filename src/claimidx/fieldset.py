"""Declared field-set versions: pull rejects a short row instead of scoring it lower.

A field-set version is part of the row or receipt's identity. Missing required
fields cannot be filled in by defaults and then ranked; the short payload is
skipped. A later, thinner check must come back as a new version. Claim v2
Literals (Relation.kind and the rest) are not widened here.
"""

from __future__ import annotations

from typing import Any

# schema/claim.v1.json required keys. nr/exp/rb/req and other optionals are not in the set.
CLAIM_FIELD_SETS: dict[int, frozenset[str]] = {
    1: frozenset({"v", "id", "fp", "cls", "err", "fix", "eval"}),
}

# schema/protocol.v2.json required keys (plus declared v, which is the field-set id).
RECEIPT_FIELD_SETS: dict[str, dict[int, frozenset[str]]] = {
    "observation": {2: frozenset({"v", "remedy_id", "proof_id", "actor", "held"})},
    "protocol_event": {2: frozenset({"v", "kind", "actor"})},
    "failure": {2: frozenset({"v", "id", "fp_v1", "family_id", "cls", "err"})},
    "remedy": {2: frozenset({"v", "failure_id", "fix", "proof_id", "own"})},
    "proof": {2: frozenset({"v", "steps"})},
    "relation": {2: frozenset({"v", "source_id", "target_id", "kind", "actor"})},
    "bundle": {2: frozenset({"v", "failure", "proof", "remedy"})},
}

_FIX_REQUIRED = frozenset({"k", "b"})
_EVAL_REQUIRED = frozenset({"cmd"})


def _int_version(raw: Any) -> int | None:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def declared_version(payload: dict[str, Any]) -> int | None:
    """v, then fieldset / fs, as an int. None if absent or unparsable."""
    for key in ("v", "fieldset", "fs"):
        if key in payload:
            return _int_version(payload.get(key))
    return None


def receipt_kind(payload: dict[str, Any]) -> str | None:
    """Classify a v2 graph receipt. Claims (which carry fp) are not receipts."""
    if "fp" in payload and "err" in payload and "fix" in payload:
        return None
    if "remedy_id" in payload and "proof_id" in payload:
        return "observation"
    if "failure" in payload and "proof" in payload and "remedy" in payload:
        return "bundle"
    if "failure_id" in payload and "proof_id" in payload and "fix" in payload:
        return "remedy"
    if "fp_v1" in payload and "family_id" in payload:
        return "failure"
    if "steps" in payload and "legacy_cmd" in payload or (payload.get("v") == 2 and "steps" in payload and "fp" not in payload and "err" not in payload):
        if "steps" in payload and "kind" not in payload and "actor" not in payload:
            return "proof"
    if "source_id" in payload and "target_id" in payload and "kind" in payload:
        return "relation"
    if payload.get("v") == 2 and "kind" in payload and "fp" not in payload and "err" not in payload and "remedy_id" not in payload:
        return "protocol_event"
    return None


UPGRADE_HINT = "field-set versions only bump on purpose; older clients skip newer rows — upgrade claimidx to read them"


def unknown_fieldset_reason(ver: int, *, kind: str | None = None) -> str:
    """Skip reason for a declared field-set version this client does not know."""
    if kind and kind not in {"claim", ""}:
        return f"unknown field-set version v{ver} for {kind}, upgrade claimidx"
    return f"unknown field-set version v{ver}, upgrade claimidx"


def fieldset_rejection(payload: dict[str, Any], *, kind: str | None = None) -> str | None:
    """Reason to skip this payload on pull, or None if the declared field-set is complete."""
    if not isinstance(payload, dict):
        return "field-set: not an object"
    kind = kind or receipt_kind(payload) or "claim"
    ver = declared_version(payload)
    if kind == "claim":
        sets = CLAIM_FIELD_SETS
        if ver is None:
            # No declared version: still enforce the v1 required keys so a short
            # row cannot slip through on defaults. Existing ledger rows all set v.
            ver = 1
        required = sets.get(ver)
        if required is None:
            return unknown_fieldset_reason(ver)
    else:
        sets_for_kind = RECEIPT_FIELD_SETS.get(kind) or {}
        if ver is None:
            ver = 2 if 2 in sets_for_kind else next(iter(sets_for_kind), None)
        if ver is None:
            return f"unknown field-set for {kind}"
        required = sets_for_kind.get(ver)
        if required is None:
            return unknown_fieldset_reason(ver, kind=kind)
    missing = [key for key in sorted(required) if key not in payload or payload.get(key) is None]
    if kind == "claim":
        fix = payload.get("fix")
        if isinstance(fix, dict):
            missing.extend(f"fix.{key}" for key in sorted(_FIX_REQUIRED) if key not in fix or fix.get(key) is None)
        ev = payload.get("eval")
        if isinstance(ev, dict):
            missing.extend(f"eval.{key}" for key in sorted(_EVAL_REQUIRED) if key not in ev or ev.get(key) is None)
    if missing:
        return f"field-set v={ver} missing {','.join(missing)}"
    return None
