"""Harness sensor: pull an error out of stdin (Claude Code hook JSON or raw stderr).

Does not apply fix.b. Fail-open: no error / secrets / parse issues → empty.
"""
from __future__ import annotations

import json
import re

from .security import SecretError, reject_secrets

_ERR_LINE = re.compile(
    r"Error|Exception|FAILED|FATAL|Traceback|ModuleNotFound|TypeError|"
    r"ImportError|EADDRINUSE|npm ERR|error:",
    re.I,
)
_SUCCESS_EVENTS = {"PostToolUse", "PreToolUse", "SessionStart", "Stop"}


def extract_hook_err(raw: str) -> tuple[str | None, str | None]:
    """Return (err, hook_event_name). Both None if there is nothing to ask."""
    text = (raw or "").strip()
    if not text:
        return None, None
    event: str | None = None
    body = text
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        event = str(obj.get("hook_event_name") or obj.get("hookEventName") or "") or None
        blobs: list[str] = []
        for key in ("tool_response", "error", "stderr", "output", "message", "content"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                blobs.append(val)
            elif isinstance(val, dict):
                for inner in ("stderr", "stdout", "output", "error", "content", "message"):
                    iv = val.get(inner)
                    if isinstance(iv, str) and iv.strip():
                        blobs.append(iv)
        body = "\n".join(blobs) if blobs else ""
        if event in _SUCCESS_EVENTS and not body:
            return None, event
        if not body:
            return None, event
    err = _first_err_line(body)
    if not err:
        return None, event
    try:
        reject_secrets(err)
    except SecretError:
        return None, event
    return err[:280], event


def _first_err_line(body: str) -> str | None:
    fallback = None
    for line in body.splitlines():
        s = line.strip()
        if not s or s.lower().startswith("traceback"):
            continue
        if not _ERR_LINE.search(s):
            continue
        if re.search(r"Error|Exception|ERR!", s, re.I):
            return s
        if fallback is None:
            fallback = s
    if fallback:
        return fallback
    cleaned = " ".join(body.split())
    if len(cleaned) < 12:
        return None
    return cleaned[:280]


def claude_context(event: str, dense: str) -> str:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": dense[:4000],
        }
    }
    return json.dumps(payload, ensure_ascii=False)
