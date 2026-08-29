"""Public projection of a local claim.

Local ingest keeps the full (secret-scanned) record. The public commons
only receives a twin that still matches (same fingerprint) but has no
notes, no local paths, no project eval recipes, no mailbox identifiers.

A private live home (CLAIMIDX_HOME_API you control) still gets the full
claim. The outbox / home-propose path — the GitHub ledger — always
projects. That is how the public library grows without shipping a
customer's tree.
"""

from __future__ import annotations

import re

from .fingerprint import normalize_error
from .models import Claim, EvalSpec, Fix
from .team import agent_slug

_EMAIL = re.compile(r"\b\S+@\S+\.\S+\b")
_EVAL_LOCAL = re.compile(
    r"(?:tests[/\\]|[A-Za-z]:[/\\]|/(?:home|Users|Users)/|\\\\|\.py\b)",
    re.I,
)
_HOSTY = re.compile(
    r"\b(?:localhost|\.internal\b|\.local\b|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b",
    re.I,
)
# Home/tmp/drive paths only. Relative aliases like ./src/* are public recipe.
# Windows drive is C:\ or C:/foo — not l:// inside postgresql:// or https://.
_ABS_PATH = re.compile(
    r"(?:[A-Za-z]:(?:\\+|/(?!/))|\\\\|~[/\\]|/(?:home|Users|usr|var|tmp|root|etc)/)[^\s'\"]+",
    re.I,
)


class PublicSkip(ValueError):
    """This claim must not leave the machine."""


def public_eval(cmd: str) -> str:
    raw = (cmd or "").strip() or "true"
    if _EVAL_LOCAL.search(raw) or _HOSTY.search(raw) or _EMAIL.search(raw):
        return "true"
    return raw[:200]


def public_tried(items: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in items or []:
        slug = agent_slug(t)[:40]
        if slug in ("agent", "") or _EVAL_LOCAL.search(t) or "/" in t or "\\" in t:
            continue
        out.append(slug)
        if len(out) >= 8:
            break
    return out


def public_fix_body(text: str) -> str:
    """Redact mailbox, internal hosts, and absolute home paths.

    Do not run error-normalization here: that rewrites pins (`pydantic>=2.7,<3`
    → `pydantic>=<N>.<N>,<<N>`), flags (`--max-old-space-size=4096`), and
    relative paths (`./src/*`) that agents need to apply the fix.
    """
    s = (text or "").strip()
    s = _EMAIL.sub("<STR>", s)
    s = _HOSTY.sub("<HOST>", s)
    s = _ABS_PATH.sub("<PATH>", s)
    return s[:4000]


def project_public(claim: Claim) -> Claim:
    """Same id + fingerprint, fields safe for the commons."""
    body = public_fix_body(claim.fix.b)
    if not body.strip():
        raise PublicSkip("fix empty after public projection")
    data = claim.model_dump()
    data["err"] = normalize_error(claim.err)
    data["note"] = ""
    data["model"] = ""
    data["tried"] = public_tried(claim.tried)
    data["tool"] = public_tried(claim.tool)
    data["fix"] = Fix(k=claim.fix.k, b=body)
    data["eval"] = EvalSpec(cmd=public_eval(claim.eval.cmd), expect=claim.eval.expect)
    data["src"] = "home"
    return Claim.model_validate(data)
