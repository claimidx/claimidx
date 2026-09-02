"""Claimidx protocol v2 graph objects.

V1 remains the wire compatibility surface.  These objects split a failure from
its possible remedies and immutable proof observations, allowing alternatives
without rewriting the public v1 ledger.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import Claim, Fix, utcnow


def _id(prefix: str) -> str:
    return prefix + secrets.token_hex(8)


def stable_id(prefix: str, material: str) -> str:
    return prefix + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def canonical_hash(payload: BaseModel | dict[str, Any]) -> str:
    raw = payload.model_dump(mode="json", exclude_none=True) if isinstance(payload, BaseModel) else payload
    body = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Applicability(BaseModel):
    os: list[str] = Field(default_factory=list, max_length=16)
    arch: list[str] = Field(default_factory=list, max_length=16)
    runtime: list[str] = Field(default_factory=list, max_length=16)
    dependency: list[str] = Field(default_factory=list, max_length=32)


ProofOp = Literal["run", "expect_exit", "observe_runtime", "expect_package"]


class ProofStep(BaseModel):
    op: ProofOp
    program: str = ""
    args: list[str] = Field(default_factory=list, max_length=64)
    code: int | None = None
    runtime: str = ""
    package: str = ""
    specifier: str = ""
    timeout_s: int = Field(default=45, ge=1, le=45)

    @model_validator(mode="after")
    def shape(self) -> ProofStep:
        if self.op == "run" and not self.program:
            raise ValueError("run proof step requires program")
        if self.op == "expect_exit" and self.code is None:
            raise ValueError("expect_exit proof step requires code")
        if self.op == "observe_runtime" and not self.runtime:
            raise ValueError("observe_runtime proof step requires runtime")
        if self.op == "expect_package" and not self.package:
            raise ValueError("expect_package proof step requires package")
        return self


class Proof(BaseModel):
    v: Literal[2] = 2
    id: str = Field(default_factory=lambda: _id("prf_"))
    steps: list[ProofStep] = Field(min_length=1, max_length=32)
    legacy_cmd: str = ""
    created: datetime = Field(default_factory=utcnow)

    @field_validator("id")
    @classmethod
    def proof_id(cls, value: str) -> str:
        if not value.startswith("prf_") or len(value) != 20:
            raise ValueError("proof id must be prf_ + 16 hex")
        return value


class Failure(BaseModel):
    v: Literal[2] = 2
    id: str
    fp_v1: str
    fp_version: Literal[1] = 1
    family_id: str
    cls: str
    err: str
    eco: str = "other"
    rt: str = ""
    dep: list[str] = Field(default_factory=list)
    features: dict[str, Any] = Field(default_factory=dict)
    created: datetime = Field(default_factory=utcnow)


class Remedy(BaseModel):
    v: Literal[2] = 2
    id: str = Field(default_factory=lambda: _id("rmd_"))
    failure_id: str
    fix: Fix
    proof_id: str
    applicability: Applicability = Field(default_factory=Applicability)
    own: str
    status: Literal["proposed", "confirmed", "contested", "stale", "rejected", "superseded"] = "proposed"
    legacy_claim_id: str = ""
    content_hash: str = ""
    key_id: str = ""
    signature: str = ""
    created: datetime = Field(default_factory=utcnow)


class Observation(BaseModel):
    v: Literal[2] = 2
    id: str = Field(default_factory=lambda: _id("obs_"))
    remedy_id: str
    proof_id: str
    actor: str
    held: bool
    replayed: bool = False
    actual_exit: int | None = None
    expected_exit: int | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    evidence_hash: str = ""
    sandbox: str = "legacy"
    key_id: str = ""
    signature: str = ""
    created: datetime = Field(default_factory=utcnow)


class Relation(BaseModel):
    v: Literal[2] = 2
    id: str = Field(default_factory=lambda: _id("rel_"))
    source_id: str
    target_id: str
    kind: Literal["alternative", "supersedes", "contradicts", "derived_from", "duplicate_of"]
    actor: str
    created: datetime = Field(default_factory=utcnow)


class Bundle(BaseModel):
    v: Literal[2] = 2
    failure: Failure
    proof: Proof
    remedy: Remedy

    @model_validator(mode="after")
    def links(self) -> Bundle:
        if self.remedy.failure_id != self.failure.id:
            raise ValueError("remedy failure_id does not match bundle failure")
        if self.remedy.proof_id != self.proof.id:
            raise ValueError("remedy proof_id does not match bundle proof")
        return self


class ProtocolEvent(BaseModel):
    v: Literal[2] = 2
    id: str = Field(default_factory=lambda: _id("evt_"))
    kind: str
    object_id: str = ""
    actor: str
    payload: dict[str, Any] = Field(default_factory=dict)
    key_id: str = ""
    signature: str = ""
    created: datetime = Field(default_factory=utcnow)

    @field_validator("actor")
    @classmethod
    def actor_is_did(cls, value: str) -> str:
        if not value.startswith("did:"):
            raise ValueError("protocol event actor must be a DID")
        return value


def proof_from_claim(claim: Claim) -> Proof:
    steps = [
        ProofStep(op="run", program="legacy", args=[claim.eval.cmd]),
        ProofStep(op="expect_exit", code=claim.eval.expect),
    ]
    proof_id = stable_id("prf_", f"{claim.eval.cmd}\n{claim.eval.expect}")
    return Proof(id=proof_id, steps=steps, legacy_cmd=claim.eval.cmd, created=claim.ts)


def failure_from_claim(claim: Claim) -> Failure:
    from .fingerprint import error_features, family_fingerprint

    return Failure(
        id=stable_id("flr_", claim.fp),
        fp_v1=claim.fp,
        family_id=family_fingerprint(err=claim.err, cls=claim.cls, eco=claim.eco),
        cls=claim.cls,
        err=claim.err,
        eco=claim.eco,
        rt=claim.rt,
        dep=list(claim.dep),
        features=error_features(claim.err),
        created=claim.ts,
    )


def remedy_from_claim(claim: Claim, failure: Failure, proof: Proof) -> Remedy:
    material = f"{failure.id}\n{claim.fix.k}\n{claim.fix.b}\n{proof.id}\n{claim.own}"
    remedy = Remedy(
        id=stable_id("rmd_", material),
        failure_id=failure.id,
        fix=claim.fix,
        proof_id=proof.id,
        applicability=Applicability(runtime=[claim.rt] if claim.rt else [], dependency=list(claim.dep)),
        own=claim.own,
        status=claim.st,
        legacy_claim_id=claim.id,
        created=claim.ts,
    )
    remedy.content_hash = canonical_hash(remedy.model_copy(update={"content_hash": "", "signature": ""}))
    return remedy
