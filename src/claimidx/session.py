"""Local agent session continuity. Not shared. Not part of the public ledger."""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from typing import Any

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


# MCP initialize may stamp this so CLI and a long-lived server can be compared.
_SERVER_INFO_VERSION: str | None = None


def set_server_info_version(version: str | None) -> None:
    """Record the running MCP server's serverInfo.version, or clear it."""
    global _SERVER_INFO_VERSION
    raw = (version or "").strip()
    _SERVER_INFO_VERSION = raw[:32] or None


def server_info_version() -> str | None:
    return _SERVER_INFO_VERSION


def version_fields(server_version: str | None = None) -> dict[str, Any]:
    """Installed package version and MCP serverInfo version, as separate fields.

    `version_mismatch` is true only when both are present and they differ.
    The CLI (no MCP server in-process) leaves server_info_version as null.
    """
    from claimidx import __version__

    pkg = __version__
    srv = server_version if server_version is not None else _SERVER_INFO_VERSION
    if isinstance(srv, str):
        srv = srv.strip() or None
    mismatch = bool(pkg and srv and pkg != srv)
    return {
        "package_version": pkg,
        "server_info_version": srv,
        "version_mismatch": mismatch,
    }
