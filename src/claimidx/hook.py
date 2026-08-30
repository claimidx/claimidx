"""Harness sensor: pull an error out of stdin (Claude Code hook JSON or raw stderr).

Does not apply fix.b. Fail-open: no error / secrets / parse issues → empty.
`claimidx init` writes Claude PostToolUseFailure and, when those configs exist,
Cursor/Grok MCP (`claimidx-mcp`). Never writes home URLs or tokens.
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


def _toml_str(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def mcp_owner_env(own: str, agent: str = "") -> dict[str, str]:
    env = {"CLAIMIDX_OWNER": own}
    if agent:
        env["CLAIMIDX_AGENT"] = agent
    return env


def cursor_mcp_path() -> Path:
    override = os.environ.get("CLAIMIDX_CURSOR_MCP")
    if override:
        return Path(override)
    return Path.home() / ".cursor" / "mcp.json"


def grok_config_path() -> Path:
    override = os.environ.get("CLAIMIDX_GROK_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".grok" / "config.toml"


def opencode_config_path() -> Path:
    override = os.environ.get("CLAIMIDX_OPENCODE_CONFIG")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "opencode" / "opencode.json"


def vscode_mcp_path() -> Path:
    override = os.environ.get("CLAIMIDX_VSCODE_MCP")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Code" / "User" / "mcp.json"
    return Path.home() / ".config" / "Code" / "User" / "mcp.json"


def _json_mcp_block(own: str, agent: str) -> dict:
    return {"command": "claimidx-mcp", "args": [], "env": mcp_owner_env(own, agent)}


def install_cursor_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Merge claimidx into Cursor mcp.json. Skip if Cursor is not installed."""
    target = path or cursor_mcp_path()
    forced = bool(os.environ.get("CLAIMIDX_CURSOR_MCP"))
    if not forced and not target.exists() and not target.parent.exists():
        return {"path": str(target), "status": "skip", "reason": "no cursor config dir"}
    target.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return {"path": str(target), "status": "error", "error": f"mcp.json is not json: {e}"}
        if not isinstance(loaded, dict):
            return {"path": str(target), "status": "error", "error": "mcp.json is not a json object"}
        data = loaded
    servers = data.get("mcpServers")
    if servers is None:
        servers = {}
        data["mcpServers"] = servers
    if not isinstance(servers, dict):
        return {"path": str(target), "status": "error", "error": "mcpServers is not an object"}
    existing = servers.get("claimidx")
    if isinstance(existing, dict) and existing.get("command") == "claimidx-mcp":
        return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
    servers["claimidx"] = _json_mcp_block(own, agent)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def install_grok_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Append [mcp_servers.claimidx] to Grok config.toml if missing. Do not rewrite the file."""
    target = path or grok_config_path()
    forced = bool(os.environ.get("CLAIMIDX_GROK_CONFIG"))
    if not forced and not target.exists():
        return {"path": str(target), "status": "skip", "reason": "no grok config"}
    target.parent.mkdir(parents=True, exist_ok=True)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    if "[mcp_servers.claimidx]" in text:
        return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
    block = (
        "\n[mcp_servers.claimidx]\n"
        'command = "claimidx-mcp"\n'
        "\n[mcp_servers.claimidx.env]\n"
        f"CLAIMIDX_OWNER = {_toml_str(own)}\n"
    )
    if agent:
        block += f"CLAIMIDX_AGENT = {_toml_str(agent)}\n"
    target.write_text((text.rstrip() + "\n" if text.strip() else "") + block.lstrip("\n"), encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def _load_json_object(target: Path, label: str) -> dict | tuple[dict, dict]:
    if not target.exists():
        return {}
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return ({"path": str(target), "status": "error", "error": f"{label} is not json: {e}"},)
    if not isinstance(loaded, dict):
        return ({"path": str(target), "status": "error", "error": f"{label} is not a json object"},)
    return loaded


def install_opencode_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Merge claimidx into OpenCode opencode.json. Skip if OpenCode is not installed."""
    target = path or opencode_config_path()
    forced = bool(os.environ.get("CLAIMIDX_OPENCODE_CONFIG"))
    if not forced and not target.exists() and not target.parent.exists():
        return {"path": str(target), "status": "skip", "reason": "no opencode config dir"}
    target.parent.mkdir(parents=True, exist_ok=True)
    loaded = _load_json_object(target, "opencode.json")
    if isinstance(loaded, tuple):
        return loaded[0]
    data = loaded
    mcp = data.get("mcp")
    if mcp is None:
        mcp = {}
        data["mcp"] = mcp
    if not isinstance(mcp, dict):
        return {"path": str(target), "status": "error", "error": "mcp is not an object"}
    existing = mcp.get("claimidx")
    if isinstance(existing, dict) and "claimidx-mcp" in str(existing.get("command") or ""):
        return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
    mcp["claimidx"] = {
        "type": "local",
        "command": ["claimidx-mcp"],
        "enabled": True,
        "environment": mcp_owner_env(own, agent),
    }
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def install_vscode_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Merge claimidx into VS Code User mcp.json. Skip if VS Code is not installed."""
    target = path or vscode_mcp_path()
    forced = bool(os.environ.get("CLAIMIDX_VSCODE_MCP"))
    if not forced and not target.exists() and not target.parent.exists():
        return {"path": str(target), "status": "skip", "reason": "no vscode user dir"}
    target.parent.mkdir(parents=True, exist_ok=True)
    loaded = _load_json_object(target, "mcp.json")
    if isinstance(loaded, tuple):
        return loaded[0]
    data = loaded
    servers = data.get("servers")
    if servers is None:
        servers = {}
        data["servers"] = servers
    if not isinstance(servers, dict):
        return {"path": str(target), "status": "error", "error": "servers is not an object"}
    existing = servers.get("claimidx")
    if isinstance(existing, dict) and existing.get("command") == "claimidx-mcp":
        return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
    servers["claimidx"] = _json_mcp_block(own, agent)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def install_harness(*, own: str, agent: str = "") -> dict:
    """Claude failure hook plus MCP into Cursor/Grok/OpenCode/VS Code when those configs exist."""
    return {
        "claude": install_claude_hook(),
        "cursor": install_cursor_mcp(own=own, agent=agent),
        "grok": install_grok_mcp(own=own, agent=agent),
        "opencode": install_opencode_mcp(own=own, agent=agent),
        "vscode": install_vscode_mcp(own=own, agent=agent),
    }
