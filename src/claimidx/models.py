from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


FixKind = Literal["pin", "patch", "config", "constraint", "cmd", "wontfix"]
Status = Literal["proposed", "confirmed", "contested", "stale", "rejected"]


def new_id() -> str:
    return "cix_" + secrets.token_hex(8)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Fix(BaseModel):
    k: FixKind
    b: str = Field(max_length=4000)

    @field_validator("b")
    @classmethod
    def no_secrets(cls, v: str) -> str:
        from .policy import reject_payload

        reject_payload(v, "fix")
        return v


class EvalSpec(BaseModel):
    cmd: str = Field(max_length=400)
    expect: int = 0

    @field_validator("cmd")
    @classmethod
    def eval_policy(cls, v: str) -> str:
        from .policy import reject_eval

        reject_eval(v)
        return v


class Claim(BaseModel):
    v: Literal[1] = 1
    id: str = Field(default_factory=new_id)
    fp: str
    cls: str
    err: str = Field(max_length=280)
    eco: str = "other"
    rt: str = ""
    dep: list[str] = Field(default_factory=list)
    tool: list[str] = Field(default_factory=list)
    tried: list[str] = Field(default_factory=list)
    fix: Fix
    eval: EvalSpec
    st: Status = "proposed"
    nc: int = 0
    nf: int = 0
    nr: int = 0  # confirms that ran confirm --replay and held
    own: str = "did:claimidx:anon"
    model: str = ""
    ts: datetime = Field(default_factory=utcnow)
    exp: datetime | None = None
    note: str = Field(default="", max_length=240)
    src: str = "local"  # local | seed | home

    @field_validator("id")
    @classmethod
    def id_shape(cls, v: str) -> str:
        prefix = v[:4]
        if prefix not in ("spr_", "cix_") or len(v) != 20:
            raise ValueError("id must be spr_|cix_ + 16 hex")
        if any(c not in "0123456789abcdef" for c in v[4:]):
            raise ValueError("id must be spr_|cix_ + 16 hex")
        return v

    @field_validator("fp")
    @classmethod
    def fp_shape(cls, v: str) -> str:
        if len(v) != 64 or any(c not in "0123456789abcdef" for c in v):
            raise ValueError("fp must be 64 lowercase hex")
        return v

    @field_validator("err", "note", "model", "own", "rt", "cls")
    @classmethod
    def strip_secrets(cls, v: str) -> str:
        from .security import reject_secrets

        reject_secrets(v)
        return v

    def score(self) -> float:
        age_days = max(0.0, (utcnow() - self.ts).total_seconds() / 86400)
        freshness = 1.0 / (1.0 + age_days / 45.0)
        conf = self.nc / (self.nc + self.nf + 1)
        status_w = {
            "confirmed": 1.0,
            "proposed": 0.35,
            "contested": 0.15,
            "stale": 0.05,
            "rejected": 0.0,
        }[self.st]
        # true/false is not a replay recipe; rank it below claims with a real eval.
        parts = (self.eval.cmd or "").strip().split()
        head = (parts[0] if parts else "true").lower()
        proof = 0.55 if head in ("true", "false") else 1.0
        return status_w * (0.55 * conf + 0.45 * freshness) * (1.0 + 0.08 * min(self.nc, 12)) * proof

    def refresh_status(self) -> Status:
        now = utcnow()
        if self.st == "rejected":
            return self.st
        if getattr(self, "src", "local") == "home":
            # remote nc/nf are hearsay; do not auto-promote
            self.st = "proposed"
            return self.st
        if self.exp and now > self.exp:
            self.st = "stale"
            return self.st
        if self.nf >= 2 and self.nf > self.nc:
            self.st = "contested"
        elif self.nc >= 2 and self.nc > self.nf:
            self.st = "confirmed"
        elif self.nc >= 1 and self.nf == 0:
            self.st = "confirmed"
        default_exp = self.ts + timedelta(days=90)
        if now > (self.exp or default_exp) and self.st == "confirmed":
            self.st = "stale"
        return self.st
