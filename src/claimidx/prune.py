"""Prune: keep only claims that can graduate.

A claim is prior art when its eval can discriminate held from miss. Under
the gate, `true`, `<tool> --version`, and other hints never mint `nr`, so a
claim that carries only a hint is a note, not a claim. Before dropping one
its eval is upgraded where the claim itself says how: a pin becomes a
version check, a missing module becomes an import, a Go package becomes
`go list`, a crate becomes `cargo pkgid`. What still cannot be replayed
after that is retired.

Counters are evidence only when a replay produced them; seed counters were
written by hand and are reset, not trusted.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .fingerprint import classify
from .models import Claim
from .policy import eval_allowed
from .public import eval_is_proof, public_eval, refine_eval
from .target import claim_target, suggest_eval


def upgrade_eval(claim: Claim) -> tuple[str, str]:
    """(eval_cmd, how): how is kept | refined | target | hint."""
    ev = (claim.eval.cmd or "").strip() or "true"
    if _proof(ev):
        return ev, "kept"
    refined = refine_eval(ev, fix_k=claim.fix.k, fix_b=claim.fix.b, dep=list(claim.dep or []), eco=claim.eco)
    if _proof(refined):
        return refined, "refined"
    target = claim_target(cls=claim.cls or classify(claim.err), err=claim.err, dep=list(claim.dep or []))
    suggested = suggest_eval(target, claim.eco) if target else ""
    if suggested and _proof(suggested):
        return suggested, "target"
    return ev, "hint"


def _proof(cmd: str) -> bool:
    """Proof the policy runs and the public projection keeps whole.

    An eval the policy refuses cannot be replayed; one that the public
    projection truncates or blanks (local paths, over 200 chars) cannot be
    shared. Prior art is both, or it is a note.
    """
    return eval_is_proof(cmd) and eval_allowed(cmd)[0] and public_eval(cmd) == cmd


def worth_keeping(claim: Claim) -> bool:
    """True when the claim's eval is proof, after the upgrade attempt."""
    return upgrade_eval(claim)[1] != "hint"


@dataclass
class PruneReport:
    seen: int = 0
    kept: int = 0
    upgraded: int = 0
    dropped: int = 0
    dropped_ids: list[str] = field(default_factory=list)
    upgraded_ids: list[str] = field(default_factory=list)
    by_owner: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "seen": self.seen,
            "kept": self.kept,
            "upgraded": self.upgraded,
            "dropped": self.dropped,
            "dropped_ids": self.dropped_ids[:50],
            "upgraded_ids": self.upgraded_ids[:50],
            "by_owner": dict(sorted(self.by_owner.items(), key=lambda kv: -kv[1])[:10]),
        }


def _decide(claim: Claim, report: PruneReport) -> tuple[str, str]:
    report.seen += 1
    ev, how = upgrade_eval(claim)
    if how == "hint":
        report.dropped += 1
        report.dropped_ids.append(claim.id)
        report.by_owner[claim.own] = report.by_owner.get(claim.own, 0) + 1
    else:
        report.kept += 1
        if how != "kept":
            report.upgraded += 1
            report.upgraded_ids.append(claim.id)
    return ev, how


def prune_store(store, *, apply: bool = False, actor: str = "did:claimidx:anon") -> PruneReport:
    """Retire local rows that cannot graduate; upgrade the evals of the rest in place."""
    report = PruneReport()
    for claim in store.all():
        ev, how = _decide(claim, report)
        if not apply:
            continue
        if how == "hint":
            store.delete(claim.id, actor=actor, reason="prune: eval is a hint after upgrade")
        elif how != "kept":
            claim.eval.cmd = ev
            store.put(claim)
            store.log("prune-upgrade", actor, claim.id, {"eval": ev, "how": how})
    return report


def prune_ledger(path: Path, retired: Path, *, apply: bool = False) -> PruneReport:
    """Rewrite a jsonl ledger: upgraded evals in place, hint rows appended to the retired ledger."""
    report = PruneReport()
    keep_lines: list[str] = []
    retire_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        try:
            claim = Claim.model_validate(raw)
        except ValidationError:
            # A row the model refuses cannot be served, let alone replayed.
            report.seen += 1
            report.dropped += 1
            report.dropped_ids.append(str(raw.get("id") or "?"))
            raw["note"] = ((raw.get("note") or "") + " [retired: row fails validation]").strip()
            retire_lines.append(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
            continue
        ev, how = _decide(claim, report)
        if how == "hint":
            raw["note"] = ((raw.get("note") or "") + " [retired: eval is a hint after upgrade]").strip()
            retire_lines.append(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
            continue
        if how != "kept":
            raw["eval"] = {"cmd": ev, "expect": int((raw.get("eval") or {}).get("expect") or 0)}
        keep_lines.append(json.dumps(raw, ensure_ascii=False, separators=(",", ":")))
    if apply:
        path.write_text("\n".join(keep_lines) + ("\n" if keep_lines else ""), encoding="utf-8", newline="\n")
        if retire_lines:
            existing = retired.read_text(encoding="utf-8") if retired.exists() else ""
            sep = "" if not existing or existing.endswith("\n") else "\n"
            retired.write_text(existing + sep + "\n".join(retire_lines) + "\n", encoding="utf-8", newline="\n")
    return report


def prune_seed_rows(rows: list[dict]) -> tuple[list[dict], PruneReport]:
    """Seed dicts (the `_SEEDS` shape) that survive, with upgraded evals and counters reset to what was replayed: nothing."""
    from .fingerprint import fingerprint, normalize_error
    from .models import EvalSpec, Fix

    report = PruneReport()
    out: list[dict] = []
    for raw in rows:
        err = raw["err"]
        cls = classify(err)
        dep = list(raw.get("dep") or [])
        eco = raw.get("eco") or "other"
        k, b = raw["fix"]
        claim = Claim(
            id=raw["id"],
            fp=fingerprint(err=err, cls=cls, eco=eco, rt=raw.get("rt") or "", dep=dep),
            cls=cls,
            err=normalize_error(err),
            eco=eco,
            rt=raw.get("rt") or "",
            dep=dep,
            fix=Fix(k=k, b=b),
            eval=EvalSpec(cmd=raw["eval"]),
            own="did:claimidx:seed",
        )
        ev, how = _decide(claim, report)
        if how == "hint":
            continue
        new = dict(raw)
        new["eval"] = ev
        new.pop("st", None)
        new.pop("nc", None)
        new.pop("nf", None)
        out.append(new)
    return out, report


def default_paths() -> tuple[Path, Path]:
    root = Path(os.environ.get("CLAIMIDX_REPO") or Path(__file__).resolve().parents[2])
    return root / "data" / "claims.jsonl", root / "data" / "claims-retired.jsonl"
