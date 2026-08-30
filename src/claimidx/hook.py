"""Harness sensor: pull an error out of stdin (Claude Code hook JSON or raw stderr).

Does not apply fix.b. Fail-open: no error / secrets / parse issues → empty.
`claimidx init` writes a Claude Code PostToolUseFailure command into settings.json.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from .security import SecretError, reject_secrets

_MARKER = "claimidx hook"

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
        for key in ("tool_response", "tool_result", "error", "stderr", "output", "message", "content", "result"):
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


def hook_command() -> str:
    exe = sys.executable
    if os.name == "nt":
        # PowerShell treats a quoted path as a string, not an invocation.
        return "& '" + exe.replace("'", "''") + "' -m claimidx hook"
    return f"{shlex.quote(exe)} -m claimidx hook"


def claude_settings_path() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override) / "settings.json"
    return Path.home() / ".claude" / "settings.json"


def claude_hook_block() -> dict:
    h: dict = {"type": "command", "command": hook_command()}
    if os.name == "nt":
        h["shell"] = "powershell"
    return {"matcher": "Bash", "hooks": [h]}


def settings_has_claimidx(data: dict) -> bool:
    for group in (data.get("hooks") or {}).get("PostToolUseFailure") or []:
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks") or []:
            if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                return True
    return False


def merge_claude_hooks(data: dict) -> dict:
    out = dict(data or {})
    raw_hooks = out.get("hooks")
    if raw_hooks is None:
        raw_hooks = {}
    if not isinstance(raw_hooks, dict):
        raise ValueError("settings.json hooks is not an object")
    hooks = dict(raw_hooks)
    raw_groups = hooks.get("PostToolUseFailure") or []
    if not isinstance(raw_groups, list):
        raise ValueError("settings.json PostToolUseFailure is not a list")
    groups = list(raw_groups)
    cmd = hook_command()
    found = False
    for group in groups:
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks") or []:
            if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                h["type"] = "command"
                h["command"] = cmd
                if os.name == "nt":
                    h["shell"] = "powershell"
                else:
                    h.pop("shell", None)
                found = True
    if not found:
        groups.append(claude_hook_block())
    hooks["PostToolUseFailure"] = groups
    out["hooks"] = hooks
    return out


def install_claude_hook(path: Path | None = None) -> dict:
    target = path or claude_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if target.exists():
        raw = target.read_text(encoding="utf-8")
        try:
            loaded = json.loads(raw)
        except json.JSONDecodeError as e:
            return {
                "path": str(target),
                "status": "error",
                "error": f"settings.json is not json: {e}",
            }
        except OSError as e:
            return {"path": str(target), "status": "error", "error": str(e)}
        if not isinstance(loaded, dict):
            return {
                "path": str(target),
                "status": "error",
                "error": "settings.json is not a json object",
            }
        data = loaded
    try:
        merged = merge_claude_hooks(data)
    except ValueError as e:
        return {"path": str(target), "status": "error", "error": str(e)}
    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return {
        "path": str(target),
        "command": hook_command(),
        "event": "PostToolUseFailure",
        "matcher": "Bash",
        "status": "installed",
    }
