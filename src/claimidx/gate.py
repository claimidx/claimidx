"""Graduation gate: the one place that decides whether a held replay mints `nr`.

Every in-process `confirm --replay` surface (CLI, MCP server, batch replay)
routes through `graduation_gate`. The home HTTP `?replay=true` path is
deliberately not here: the home never runs eval.cmd, it records the agent's
asserted replay, and it has nothing on disk to bind or hash.

Checks live here, in order, and each returns the first refusal:

1. env       claim.rt must match the observed executing runtime (python/node)
2. target    the eval must observe the claimed target            [X1, pending]
3. binding   proof artifact digests must still match under --cwd [X2, pending]
4. digest    observed dependency digests drift -> warn           [I1, pending]

Source of the contract: `Validated Results/` and tests/test_graduation_gate.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .sandbox import ReplayResult, replay_records_hold

if TYPE_CHECKING:
    from .models import Claim
    from .store import Store


@dataclass
class GateDecision:
    mint_nr: bool
    reason: str
    warns: list[str] = field(default_factory=list)

    def as_tuple(self) -> tuple[bool, str]:
        return self.mint_nr, self.reason


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
    return GateDecision(True, "held")
