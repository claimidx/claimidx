"""Optional hangout/channel attribution for share and commons events.

Social/Growth set `CLAIMIDX_CHANNEL` / `CLAIMIDX_SOURCE` (or CLI/MCP flags) so
stranger DID shares are attributable. Labels only — same shape hangout already
uses (slug tokens). Omitted when unset; consumers treat missing as `unknown`.
No PII beyond those labels.
"""

from __future__ import annotations

import os
import re
from typing import Any

_LABEL = re.compile(r"[^a-z0-9._+-]+")
_MAX_LEN = 64


def sanitize_label(raw: str | None) -> str:
    """Hangout-safe attribution token: lowercase slug, max 64 chars."""
    s = (raw or "").strip().lower().replace(" ", "-").replace("/", "-")
    s = _LABEL.sub("-", s).strip("-._+")
    return s[:_MAX_LEN]


def resolve_attribution(
    *,
    channel: str | None = None,
    source: str | None = None,
) -> dict[str, str]:
    """Resolve optional channel/source for share events.

    Precedence: explicit args → env (`CLAIMIDX_CHANNEL` / `CLAIMIDX_SOURCE`) →
    config `channel` / `source`. Empty when nothing is set (do not invent
    `unknown` here — leave that to readers).
    """
    ch = sanitize_label(channel)
    src = sanitize_label(source)
    if not ch:
        ch = sanitize_label(os.environ.get("CLAIMIDX_CHANNEL"))
    if not src:
        src = sanitize_label(os.environ.get("CLAIMIDX_SOURCE"))
    if not ch or not src:
        try:
            from .config import get as cfg_get

            if not ch:
                ch = sanitize_label(str(cfg_get("channel", "") or ""))
            if not src:
                src = sanitize_label(str(cfg_get("source", "") or ""))
        except Exception:
            pass
    out: dict[str, str] = {}
    if ch:
        out["channel"] = ch
    if src:
        out["source"] = src
    return out


def merge_attribution(detail: dict[str, Any] | None, attrib: dict[str, str] | None) -> dict[str, Any] | None:
    """Merge attribution into an event/outbox detail dict. Returns None only when both empty."""
    base = dict(detail or {})
    for k, v in (attrib or {}).items():
        if v and k not in base:
            base[k] = v
    return base or None
