from __future__ import annotations

from datetime import datetime

from .models import Claim, EvalSpec, Fix


def _ts(dt: datetime | None) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y%m%dT%H%M%SZ")


def encode(c: Claim) -> str:
    lines = [
        "CLAIMIDX 1",
        f"id {c.id}",
        f"fp {c.fp}",
        f"cls {c.cls}",
        f"err {c.err}",
        f"eco {c.eco}",
        f"rt {c.rt}",
        f"dep {'|'.join(c.dep)}",
        f"tool {','.join(c.tool)}",
        f"tried {','.join(c.tried)}",
        f"fix.k {c.fix.k}",
        "fix.b " + c.fix.b.replace("\n", "\\n"),
        f"eval {c.eval.cmd}",
        f"expect {c.eval.expect}",
        f"st {c.st}",
        f"nc {c.nc}",
        f"nf {c.nf}",
        f"own {c.own}",
        f"model {c.model}",
        f"ts {_ts(c.ts)}",
        f"exp {_ts(c.exp)}",
        f"note {c.note}",
        f"src {getattr(c, 'src', 'local')}",
    ]
    return "\n".join(lines) + "\n"


def decode(text: str) -> Claim:
    head = text.splitlines()[0] if text else ""
    if head not in ("CLAIMIDX 1", "SPOOR 1"):  # SPOOR 1: pre-rename dense header on existing claims
        raise ValueError("not a Claimidx dense document")
    kv: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        key, _, rest = line.partition(" ")
        kv[key] = rest
    from datetime import timezone

    def parse_ts(s: str) -> datetime | None:
        if not s:
            return None
        return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)

    return Claim(
        id=kv["id"],
        fp=kv["fp"],
        cls=kv["cls"],
        err=kv["err"],
        eco=kv.get("eco") or "other",
        rt=kv.get("rt") or "",
        dep=[p for p in kv.get("dep", "").split("|") if p],
        tool=[p for p in kv.get("tool", "").split(",") if p],
        tried=[p for p in kv.get("tried", "").split(",") if p],
        fix=Fix(k=kv.get("fix.k", "cmd"), b=kv.get("fix.b", "").replace("\\n", "\n")),  # type: ignore[arg-type]
        eval=EvalSpec(cmd=kv.get("eval", "true"), expect=int(kv.get("expect") or 0)),
        st=kv.get("st") or "proposed",  # type: ignore[arg-type]
        nc=int(kv.get("nc") or 0),
        nf=int(kv.get("nf") or 0),
        own=kv.get("own") or "did:claimidx:anon",
        model=kv.get("model") or "",
        ts=parse_ts(kv.get("ts", "")) or Claim.model_fields["ts"].default_factory(),  # type: ignore[misc]
        exp=parse_ts(kv.get("exp", "")),
        note=kv.get("note") or "",
        src=kv.get("src") or "local",
    )
