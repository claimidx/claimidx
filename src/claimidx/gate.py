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
        return GateDecision(False, why)
    target = claim_target(cls=claim.cls, err=claim.err, dep=claim.dep)
    if target and not eval_observes_target(claim.eval.cmd, target) and not proof_observes_target(_proof_steps(store, claim.id), target):
        suggest = suggest_eval(target, claim.eco)
        return GateDecision(
            False,
            f"eval does not observe claimed target '{target}'",
            suggest={"eval": suggest} if suggest else {},
        )
    return GateDecision(True, "held")
