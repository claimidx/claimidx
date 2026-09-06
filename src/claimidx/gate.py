"""Graduation gate: the one place that decides whether a held replay mints `nr`.

Every in-process `confirm --replay` surface (CLI, MCP server, batch replay)
routes through `graduation_gate`. The home HTTP `?replay=true` path is
deliberately not here: the home never runs eval.cmd, it records the agent's
asserted replay, and it has nothing on disk to bind or hash.

Checks live here, in order, and each returns the first refusal:

1. env       claim.rt must match the observed executing runtime (python/node)
2. target    the eval must observe the claimed target            [X1]
3. binding   proof artifact digests must still match under --cwd [X2, pending]
4. digest    observed dependency digests drift -> warn           [I1, pending]

Source of the contract: `Validated Results/` and tests/test_graduation_gate.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .sandbox import ReplayResult, replay_records_hold
from .target import claim_target, eval_observes_target, proof_observes_target, suggest_eval

if TYPE_CHECKING:
    from .models import Claim
    from .store import Store


@dataclass
class GateDecision:
    mint_nr: bool
    reason: str
    warns: list[str] = field(default_factory=list)
    suggest: dict[str, str] = field(default_factory=dict)  # the passing form, when a refusal has one

    def as_tuple(self) -> tuple[bool, str]:
        return self.mint_nr, self.reason

    def refusal(self) -> dict:
        """Payload callers merge into a not-recorded response."""
        out: dict = {"reason": self.reason}
        if self.suggest:
            out["suggest"] = self.suggest
        if self.warns:
            out["warn"] = list(self.warns)
        return out


def _proof_steps(store: Store | None, claim_id: str) -> list[dict]:
    if store is None:
        return []
    try:
        graph = store.graph(claim_id)
    except Exception:
        return []
    proof = (graph or {}).get("proof") or {}
    return list(proof.get("steps") or [])


def _env_suggestion(claim: Claim, result: ReplayResult, why: str) -> dict[str, str]:
    """What would make this hold count: the rt the replay actually observed."""
    observed = (result.env or "").strip()
    if not observed:
        return {"hint": "run the eval with a python/node head so the executing runtime is observed"}
    if "requires rt" in why or "requires observed env" in why:
        return {"rt": observed, "hint": f"re-publish with --rt {observed} (claimidx publish --force ...) or run confirm under the claimed runtime"}
    if "mismatch" in why:
        return {
            "rt": observed,
            "hint": f"claim.rt={claim.rt} but this replay ran under {observed}: confirm under {claim.rt}, or re-publish --force --rt {observed}",
        }
    return {}


def hint_refusal(claim: Claim, result: ReplayResult, *, cwd: str | None = None) -> dict:
    """Payload for a replay that could not run as proof: reason plus the passing form."""
    reason = result.reason or "eval is a hint"
    out: dict = {"reason": reason}
    suggest: dict[str, str] = {}
    if reason.startswith("eval-untrusted"):
        suggest["hint"] = (
            "this claim was not published here; its eval is outside the portable proof grammar (imports, version checks, "
            "build/test recipes on your own tree). Read eval.cmd, then confirm --replay --trust-eval to run it deliberately"
        )
        suggest["eval"] = claim.eval.cmd
    elif reason.startswith("eval-precondition"):
        want = reason.split("no ", 1)[-1].split(" in cwd")[0] if "no " in reason else ""
        suggest["hint"] = f"run confirm --replay --cwd <tree with {want or 'the project markers'}>"
        if cwd:
            suggest["cwd"] = cwd
    else:
        target = claim_target(cls=claim.cls, err=claim.err, dep=claim.dep)
        ev = suggest_eval(target, claim.eco) if target else ""
        if not ev:
            from .public import refine_eval

            refined = refine_eval(claim.eval.cmd, fix_k=claim.fix.k, fix_b=claim.fix.b, dep=claim.dep, eco=claim.eco)
            ev = refined if refined != claim.eval.cmd else ""
        if ev:
            suggest["eval"] = ev
            suggest["hint"] = f"re-publish with --force --eval {ev!r}; `{claim.eval.cmd}` cannot discriminate held from miss"
        else:
            suggest["hint"] = "supply a discriminating eval that observes the failure (import, build, or test command)"
    out["suggest"] = suggest
    return out


def graduation_gate(
    claim: Claim,
    result: ReplayResult,
    *,
    cwd: str | None = None,
    store: Store | None = None,
) -> GateDecision:
    """Decide whether `result` (a replay of `claim.eval`) may mint `nr`.

    Callers have already handled `result.is_hint()` and `not result.held`;
    this gate only sees holds that ran.
    """
    ok, why = replay_records_hold(claim.rt, result, claim.eval.cmd)
    if not ok:
        return GateDecision(False, why, suggest=_env_suggestion(claim, result, why))
    target = claim_target(cls=claim.cls, err=claim.err, dep=claim.dep)
    if target and not eval_observes_target(claim.eval.cmd, target) and not proof_observes_target(_proof_steps(store, claim.id), target):
        suggest = suggest_eval(target, claim.eco)
        return GateDecision(
            False,
            f"eval does not observe claimed target '{target}'",
            suggest={"eval": suggest, "hint": f"re-publish with --force --eval {suggest!r}"}
            if suggest
            else {"hint": f"use an eval that imports or exercises {target}"},
        )
    return GateDecision(True, "held")
