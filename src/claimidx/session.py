"""Local agent session continuity. Not shared. Not part of the public ledger."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime

_PROCESS_SESSION: str | None = None


def session_id() -> str:
    """Precedence: CLAIMIDX_SESSION env, else a stable id for this process."""
    global _PROCESS_SESSION
    env = (os.environ.get("CLAIMIDX_SESSION") or "").strip()
    if env:
        return env[:120]
    if _PROCESS_SESSION is None:
        _PROCESS_SESSION = "sess_" + secrets.token_hex(8)
    return _PROCESS_SESSION


def utc_ts() -> str:
    return datetime.now(UTC).isoformat()
