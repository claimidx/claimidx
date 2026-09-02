"""Structured, shell-free proof validation and replay."""

from __future__ import annotations

import importlib.metadata
import json
import shlex
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from .graph import Proof, ProofStep
from .policy import ALLOWED_EVAL_HEADS, PolicyError, _norm_head
from .sandbox import ReplayResult, replay


def proof_from_legacy(cmd: str, expect: int = 0) -> Proof:
    return Proof(
        steps=[
            ProofStep(op="run", program="legacy", args=[cmd]),
            ProofStep(op="expect_exit", code=expect),
        ],
        legacy_cmd=cmd,
    )


def load_proof(path: str | Path) -> Proof:
    return Proof.model_validate_json(Path(path).read_text(encoding="utf-8"))


def validate_proof(proof: Proof) -> None:
    runs = [step for step in proof.steps if step.op == "run"]
    if len(runs) != 1:
        raise PolicyError("structured proof requires exactly one run step")
    run = runs[0]
    if run.program == "legacy":
        if len(run.args) != 1 or not proof.legacy_cmd:
            raise PolicyError("legacy proof requires one canonical command")
        return
    if _norm_head(run.program) not in ALLOWED_EVAL_HEADS:
        raise PolicyError(f"proof program not allowlisted: {_norm_head(run.program)}")
    if any("\x00" in arg for arg in run.args):
        raise PolicyError("proof argument contains NUL")


def _command(step: ProofStep, proof: Proof) -> str:
    if step.program == "legacy":
        return proof.legacy_cmd
    return shlex.join([step.program, *step.args])


def run_proof(proof: Proof, *, cwd: str | Path | None = None) -> dict[str, Any]:
    validate_proof(proof)
    run = next(step for step in proof.steps if step.op == "run")
    expected_steps = [step for step in proof.steps if step.op == "expect_exit"]
    expected = expected_steps[-1].code if expected_steps else 0
    result: ReplayResult = replay(
        _command(run, proof),
        int(expected or 0),
        timeout=float(run.timeout_s),
        cwd=str(cwd) if cwd else None,
    )
    checks: list[dict[str, Any]] = []
    held = result.held
    for step in proof.steps:
        if step.op == "observe_runtime":
            ok = not step.runtime or result.env.startswith(step.runtime)
            checks.append({"op": step.op, "expected": step.runtime, "observed": result.env, "held": ok})
            held = held and ok
        elif step.op == "expect_package":
            try:
                version = importlib.metadata.version(step.package)
                ok = not step.specifier or version in SpecifierSet(step.specifier)
            except (importlib.metadata.PackageNotFoundError, InvalidSpecifier):
                version, ok = "", False
            checks.append({"op": step.op, "package": step.package, "specifier": step.specifier, "observed": version, "held": ok})
            held = held and ok
    payload = result.as_dict()
    payload.update({"v": 2, "proof_id": proof.id, "held": held, "checks": checks, "sandbox": "argv-allowlist"})
    return payload


def proof_template(program: str, args: list[str], *, expect_exit: int = 0) -> Proof:
    proof = Proof(
        steps=[
            ProofStep(op="run", program=program, args=args),
            ProofStep(op="expect_exit", code=expect_exit),
        ]
    )
    validate_proof(proof)
    return proof


def dump_proof(proof: Proof) -> str:
    return json.dumps(proof.model_dump(mode="json"), indent=2, ensure_ascii=False, default=str)

