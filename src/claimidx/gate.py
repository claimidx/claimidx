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

import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .binding import binding_drift, compute_binding, digest_drift, is_tree_scoped
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


_MISSING_MOD = re.compile(r"(?:no module named|cannot find module)\s*['\"]?([@A-Za-z0-9_./-]+)", re.I)


def _patch_unapplied(fix_b: str, cwd: str | None) -> str:
    """For a `diff --git` remedy: the first file under cwd whose post-image lines are absent, else ""."""
    if not fix_b.lstrip().startswith("diff --git"):
        return ""
    root = os.path.abspath(cwd or os.getcwd())
    target = ""
    added: list[str] = []
    checks: list[tuple[str, list[str]]] = []
    for line in fix_b.splitlines():
        if line.startswith("+++ "):
            if target:
                checks.append((target, added))
            target = line[4:].strip()
            target = target[2:] if target.startswith("b/") else target
            added = []
        elif line.startswith("+") and not line.startswith("+++") and target:
            if line[1:].strip():
                added.append(line[1:].rstrip())
    if target:
        checks.append((target, added))
    for path, lines in checks:
        if not lines or ".." in path.split("/"):
            continue
        try:
            text = open(os.path.join(root, path), encoding="utf-8", errors="replace").read()
        except OSError:
            return path
        if any(ln not in text for ln in lines):
            return path
    return ""


def unapplied_refusal(claim: Claim, result: ReplayResult, *, cwd: str | None = None) -> dict | None:
    """A miss that only says the fix is not applied here is not evidence against the remedy.

    `import tomli` missing when the remedy *is* `tomli==2.4.1` means nobody
    installed it yet; a `diff --git` remedy whose added lines are not in the
    tree was never applied. Returns a not-recorded payload with the apply
    step, or None when the miss is a real one.
    """
    if result.held or not result.ran:
        return None
    if claim.fix.k == "patch":
        missing_in = _patch_unapplied(claim.fix.b, cwd)
        if missing_in:
            return {
                "reason": f"fix-not-applied: the patch in fix.b is not present in {missing_in}",
                "suggest": {"hint": "save fix.b to a file and `git apply` it in --cwd, then confirm --replay again; nothing was recorded"},
            }
    blob = (result.stderr or "") + "\n" + (result.stdout or "")
    m = _MISSING_MOD.search(blob)
    if not m:
        return None
    missing = m.group(1).strip().rstrip(".").lower()
    wanted: set[str] = set()
    target = claim_target(cls=claim.cls, err=claim.err, dep=claim.dep)
    if target:
        wanted.add(target.lower())
    if claim.fix.k in {"pin", "constraint"}:
        from .public import _pkg_token

        name = _pkg_token(claim.fix.b.splitlines()[0] if claim.fix.b else "")
        if name:
            wanted.add(name.lower())
    variants = {w for base in wanted for w in (base, base.replace("-", "_"), base.replace("_", "-"), base.split(".")[0], base.split("/")[0])}
    if missing not in variants and missing.split(".")[0] not in variants:
        return None
    return {
        "reason": f"fix-not-applied: {m.group(1)} is missing here, so the eval cannot hold yet",
        "suggest": {"fix": claim.fix.b.splitlines()[0][:200], "hint": f"apply fix.b ({claim.fix.k}), then confirm --replay again; nothing was recorded"},
    }


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
    strict_digest: bool | None = None,
    actor: str = "did:claimidx:anon",
) -> GateDecision:
    """Decide whether `result` (a replay of `claim.eval`) may mint `nr`.

    Callers have already handled `result.is_hint()` and `not result.held`;
    this gate only sees holds that ran. `store` is needed for the binding and
    digest checks (they live on the v2 Proof); without it those are skipped.
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
    warns: list[str] = []
    proof = store.proof_for(claim.id) if store is not None else None
    if is_tree_scoped(claim.eval.cmd):
        cwd = cwd or os.getcwd()  # the replay ran there; bind to the same place
        if proof is not None and proof.binding is not None:
            drift = binding_drift(proof.binding, cwd)
            if drift:
                if store is not None:
                    store.log("proof-drift", actor, claim.id, {"paths": drift})
                return GateDecision(
                    False,
                    "proof-artifact-drift: " + ", ".join(drift[:8]),
                    suggest={"hint": "the recipe's files changed since the proof was bound; if that is your fix, re-publish --force --cwd <tree> to re-bind"},
                )
        elif proof is not None and store is not None:
            binding = compute_binding(claim.eval.cmd, cwd, source="first-replay")
            if binding is None:
                return GateDecision(
                    False,
                    "unbound-proof: nothing under --cwd to bind this recipe to",
                    suggest={"hint": "the eval names no file under --cwd and the tree has no manifest; point --cwd at the project"},
                )
            store.bind_proof(claim.id, binding, actor=actor)
            warns.append("proof bound on first replay (trust-on-first-use): " + ", ".join(a.path for a in binding.artifacts[:6]))
    if proof is not None and proof.observed_digest:
        drift = digest_drift(proof.observed_digest, cwd, claim.eco)
        if drift:
            strict = strict_digest if strict_digest is not None else (os.environ.get("CLAIMIDX_STRICT_DIGEST") or "").strip() in {"1", "true", "yes"}
            msg = "digest_drift: local bytes differ from the observed digest under the same pin: " + ", ".join(drift[:8])
            if strict:
                return GateDecision(
                    False, msg, suggest={"hint": "re-install the pinned artifact, or re-publish --force --observe-digest if the new bytes are the fix"}
                )
            warns.append(msg)
    return GateDecision(True, "held", warns=warns)
