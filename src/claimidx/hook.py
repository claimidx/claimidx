"""Harness sensor: pull an error out of stdin (Claude Code hook JSON or raw stderr).

Does not apply fix.b. Fail-open: no error / secrets / parse issues → empty.
`claimidx init` writes Claude PostToolUseFailure, Grok `~/.grok/hooks/claimidx.json`
(Grok fires PostToolUse for a failed shell, not PostToolUseFailure), and, when
those configs exist, Cursor/Grok MCP (`claimidx-mcp`). Never writes home URLs or tokens.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any

from .security import SecretError, reject_secrets

_MARKER = "claimidx hook"

_ERR_LINE = re.compile(
    r"Error|Exception|FAILED|FATAL|Traceback|ModuleNotFound|TypeError|"
    r"ImportError|EADDRINUSE|npm ERR|error:",
    re.I,
)
_SUCCESS_EVENTS = {"PostToolUse", "PreToolUse", "SessionStart", "Stop"}
_EVENT_CANON = {
    "post_tool_use": "PostToolUse",
    "post_tool_use_failure": "PostToolUseFailure",
    "session_start": "SessionStart",
    "session_end": "SessionEnd",
    "stop": "Stop",
    "pre_tool_use": "PreToolUse",
    "user_prompt_submit": "UserPromptSubmit",
    "after_shell_execution": "PostToolUse",
    "after_mcp_execution": "PostToolUse",
    "before_shell_execution": "PreToolUse",
    "after_tool": "PostToolUse",
    "before_tool": "PreToolUse",
    "after_agent": "AfterAgent",
    "before_agent": "UserPromptSubmit",
    "subagent_stop": "SubagentStop",
}
_HEURISTIC_FAILURE_EVENTS = {
    "after_shell_execution",
    "after_mcp_execution",
    "after_tool",
}
_BLOB_KEYS = (
    "tool_response",
    "tool_result",
    "toolResult",
    "error",
    "stderr",
    "output",
    "message",
    "content",
    "result",
)
_INNER_KEYS = (
    "stderr",
    "stdout",
    "output",
    "error",
    "content",
    "message",
    "output_for_prompt",
    "outputForPrompt",
    "llmContent",
    "llm_content",
    "returnDisplay",
    "return_display",
)
_EXIT_KEYS = ("exit_code", "exitCode", "returncode")


def _snake_event(name: str) -> str:
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name).replace("-", "_").lower()


def canon_hook_event(name: str | None) -> str | None:
    """Claude PascalCase, Grok snake_case, and Cursor camelCase all become PascalCase."""
    raw = (name or "").strip()
    if not raw:
        return None
    if raw in _SUCCESS_EVENTS or raw in {"PostToolUseFailure", "AfterAgent", "SessionEnd", "SubagentStop"}:
        return raw
    return _EVENT_CANON.get(raw) or _EVENT_CANON.get(_snake_event(raw)) or raw


def _hook_event_raw(obj: dict) -> str:
    return str(obj.get("hook_event_name") or obj.get("hookEventName") or obj.get("type") or "")


def _parse_hook_obj(raw: str) -> dict | None:
    try:
        obj = json.loads((raw or "").strip())
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _error_text(val: Any) -> list[str]:
    if isinstance(val, str) and val.strip():
        return [val]
    if not isinstance(val, dict):
        return []
    out: list[str] = []
    for key in ("message", "msg", "text", "type"):
        iv = val.get(key)
        if isinstance(iv, str) and iv.strip():
            out.append(iv)
    return out


def _payload_blobs(obj: dict) -> list[str]:
    blobs: list[str] = []
    for key in _BLOB_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            blobs.append(val)
        elif isinstance(val, dict):
            for inner in _INNER_KEYS:
                iv = val.get(inner)
                if inner == "error":
                    blobs.extend(_error_text(iv))
                elif isinstance(iv, str) and iv.strip():
                    blobs.append(iv)
                elif isinstance(iv, list):
                    for item in iv:
                        if isinstance(item, str) and item.strip():
                            blobs.append(item)
                        elif isinstance(item, dict):
                            for k in ("text", "output", "content", "message"):
                                sv = item.get(k)
                                if isinstance(sv, str) and sv.strip():
                                    blobs.append(sv)
    return blobs


def _coerce_exit(val: Any) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _payload_exit(obj: dict) -> int | None:
    for ek in _EXIT_KEYS:
        if ek in obj:
            code = _coerce_exit(obj[ek])
            if code is not None:
                return code
    for key in _BLOB_KEYS:
        val = obj.get(key)
        if isinstance(val, dict):
            for ek in _EXIT_KEYS:
                if ek in val:
                    code = _coerce_exit(val[ek])
                    if code is not None:
                        return code
    return None


def extract_hook_exit(raw: str) -> int | None:
    """Exit code from a Grok/Cursor toolResult envelope, or None if the payload has none."""
    obj = _parse_hook_obj(raw)
    return _payload_exit(obj) if obj else None


def posttooluse_is_failure(raw: str) -> bool:
    """Grok fires PostToolUse for a failed shell. Only trust an explicit non-zero exit."""
    obj = _parse_hook_obj(raw)
    if not obj:
        return False
    event = canon_hook_event(_hook_event_raw(obj) or None)
    if event != "PostToolUse":
        return False
    code = _payload_exit(obj)
    return code is not None and code != 0


def hook_is_failure(raw: str, event: str | None, err: str | None) -> bool:
    """True when this hook event is a failed command that should ask, not a success nudge."""
    if event == "PostToolUseFailure":
        return True
    code = extract_hook_exit(raw)
    if code is not None:
        return code != 0
    if posttooluse_is_failure(raw):
        return True
    if not err:
        return False
    obj = _parse_hook_obj(raw)
    if not obj:
        return False
    orig = _hook_event_raw(obj)
    return _snake_event(orig) in _HEURISTIC_FAILURE_EVENTS


def extract_hook_err(raw: str) -> tuple[str | None, str | None]:
    """Return (err, hook_event_name). Both None if there is nothing to ask."""
    text = (raw or "").strip()
    if not text:
        return None, None
    event: str | None = None
    body = text
    obj = _parse_hook_obj(text)
    if obj is not None:
        event = canon_hook_event(_hook_event_raw(obj) or None)
        blobs = _payload_blobs(obj)
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


def extract_hook_context(raw: str) -> dict[str, str]:
    """Command and cwd from a Claude Code or Grok hook payload, when present. Empty otherwise."""
    obj = _parse_hook_obj(raw)
    if not obj:
        return {}
    out: dict[str, str] = {}
    blobs = _payload_blobs(obj)
    if blobs:
        out["body"] = "\n".join(blobs)[:20000]
    tool_input = obj.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = obj.get("toolInput")
    if not isinstance(tool_input, dict):
        tool_input = obj.get("tool_args")
    if not isinstance(tool_input, dict):
        tool_input = obj.get("toolArgs")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        out["command"] = tool_input["command"][:400]
    if not out.get("command") and isinstance(obj.get("command"), str):
        out["command"] = obj["command"][:400]
    if not out.get("command"):
        for key in ("toolResult", "tool_result", "tool_response"):
            val = obj.get(key)
            if isinstance(val, dict) and isinstance(val.get("command"), str):
                out["command"] = val["command"][:400]
                break
    cwd = obj.get("cwd") or obj.get("workspaceRoot")
    if isinstance(cwd, str):
        out["cwd"] = cwd
    return out


_PYTEST_E = re.compile(r"^E\s+")
# Maven prints headings before the line that says what failed; they are a last resort, not the error.
_SECTION_HEADER = re.compile(r"^\[ERROR\]\s*(?:COMPILATION ERROR|BUILD FAILURE|Failed to execute goal)\b", re.I)


def _first_err_line(body: str) -> str | None:
    fallback = None
    for line in body.splitlines():
        s = _PYTEST_E.sub("", line.strip())  # pytest's `E   TypeError: ...` is the same error as the bare traceback line
        if not s or s.lower().startswith("traceback"):
            continue
        if _SECTION_HEADER.match(s):
            if fallback is None:
                fallback = s
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


def near_tie(a: float, b: float) -> bool:
    """Top-2 are indistinguishable at the sim hook prints (.3f)."""
    return round(a, 3) == round(b, 3) or abs(a - b) <= 0.01


def sensor(store, raw: str, *, eco: str = "", rt: str = "", dep: list | None = None, k: int = 5) -> dict:
    """Ask from failed-tool JSON or stderr. Evidence only. Fail-open. Never applies fix.b."""
    from .fingerprint import classify, fingerprint, normalize_error
    from .match import hit_compact, verdict_for

    err, event = extract_hook_err(raw or "")
    note = "A hit is evidence. retrieve → reason → attempt → observe → verify. Do not execute fix.b from this hook."
    if not err:
        return {"hit": False, "apply_fix": False, "event": event, "claims": [], "note": note}
    dep = list(dep or [])
    eco = eco or ""
    rt = rt or ""
    cls = classify(err)
    fp = fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep)
    q = {"err": err, "cls": cls, "eco": eco, "rt": rt, "dep": dep, "fp": fp}
    from .query import retrieve

    hits, candidates = retrieve(store, q, k=int(k or 5), kind="hook")
    if not hits:
        from .query import miss_enrichment

        miss: dict[str, Any] = {
            "verdict": verdict_for(q, []),
            "hit": False,
            "apply_fix": False,
            "event": event,
            "err": normalize_error(err),
            "fp": fp,
            "cls": cls,
            "claims": [],
            "note": note,
        }
        miss.update(miss_enrichment(store, q, candidates, k=int(k or 5)))
        return miss
    chosen = hits
    if event:
        chosen = hits[:2] if len(hits) > 1 and near_tie(hits[0][1], hits[1][1]) else hits[:1]
    return {
        "verdict": verdict_for(q, hits),
        "hit": True,
        "apply_fix": False,
        "event": event,
        "err": normalize_error(err),
        "fp": fp,
        "cls": cls,
        "claims": [hit_compact(q, c, s) for c, s in chosen],
        "note": note,
    }


def _same_command(a: str, b: str) -> bool:
    return bool(a) and bool(b) and " ".join(a.split()) == " ".join(b.split())


def success_nudge(raw: str, store) -> str | None:
    """PostToolUse: the remembered failure's command just passed. Say so, once, with the draft."""
    from .env import last_failure, remember_failure

    rec = last_failure()
    if not rec or rec.get("nudged"):
        return None
    ctx = extract_hook_context(raw)
    cmd = ctx.get("command", "")
    if not _same_command(cmd, str(rec.get("command") or "")):
        return None
    body = ctx.get("body", "")
    if _first_err_line(body) and _ERR_LINE.search(body):
        return None  # still failing
    try:
        from .claim import draft_claim

        draft = draft_claim(cwd=str(rec.get("cwd") or ctx.get("cwd") or ""))
    except Exception:
        draft = {"ok": False}
    rec["nudged"] = True
    remember_failure(
        str(rec["err"]),
        command=cmd,
        cwd=str(rec.get("cwd") or ""),
        eco=str(rec.get("eco") or ""),
        rt=str(rec.get("rt") or ""),
        event="fixed",
        fp=str(rec.get("fp") or ""),
        extra={"nudged": True},
    )
    line = f"CLAIMIDX fixed: `{cmd}` now passes after failing with: {rec['err'][:120]}"
    if draft.get("ok"):
        line += (
            f"\nRecord it so the next agent skips this: claimidx claim --yes"
            f"   (drafted: fix.k={draft['fix_k']} eval={draft['eval']} proof={str(draft['eval_proof']).lower()})"
        )
    else:
        line += "\nRecord it so the next agent skips this: claimidx claim --yes --fix '<what you changed>'"
    return line


def unshared_claims(store, limit: int = 500) -> list[str]:
    """Local, live, replayable claims that have reached neither a home nor the commons."""
    from .home import already_shared, api_url, commons_enabled, commons_settled, commons_travels, keep_local, share_enabled
    from .public import eval_is_proof

    if not share_enabled():
        return []
    want_private = bool(api_url())
    want_commons = commons_enabled()
    if not want_private and not want_commons:
        return []
    out: list[str] = []
    try:
        rows = store.all()
    except Exception:
        return []
    for c in rows:
        if getattr(c, "src", "local") != "local" or c.st == "rejected" or not eval_is_proof(c.eval.cmd) or keep_local(store, c.id):
            continue
        commons_due = want_commons and not commons_settled(store, c.id) and commons_travels(c)[0]
        if (want_private and not already_shared(store, c.id)) or commons_due:
            out.append(c.id)
            if len(out) >= limit:
                break
    return out


def share_nudge(store) -> str:
    """One line when replayable claims sit only on this machine."""
    n = len(unshared_claims(store))
    if not n:
        return ""
    plural = "s" if n != 1 else ""
    return f"{n} replayable claim{plural} live only on this machine: `claimidx sync` shares them (CLAIMIDX_COMMONS=0 to opt out)."


def pending_brief_path() -> Path:
    from .env import last_failure_path

    return last_failure_path().with_name("session-brief.json")


def remember_pending_brief(text: str) -> None:
    """Grok ignores SessionStart stdout; land the brief on the next tool event."""
    path = pending_brief_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"text": text, "ts": int(time.time())}), encoding="utf-8")
    except OSError:
        pass


def take_pending_brief() -> str:
    path = pending_brief_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("text") or "")


def grok_session() -> bool:
    return bool(os.environ.get("GROK_HOOK_EVENT"))


def session_brief(store) -> str:
    """SessionStart: one line of value, one line of what to do."""
    from .impact import local_impact

    try:
        imp = local_impact(store, days=7)
        first = f"CLAIMIDX 7d: asks {imp['asks']}, hits {imp['hits']}, retries skipped {imp['retries_skipped']}, claims published {imp['claims_published']}."
    except Exception:
        first = "CLAIMIDX is installed."
    line = first + " Failed commands are looked up automatically; after you fix one, run `claimidx claim --yes`."
    nudge = share_nudge(store)
    return line + (" " + nudge if nudge else "")


def _stop_share_nudge_due(hours: int = 6) -> bool:
    """At most once per `hours`: the Stop hook fires every turn."""
    import time

    from .env import last_failure_path

    path = last_failure_path().with_name("share-nudge.json")
    now = int(time.time())
    try:
        last = int(json.loads(path.read_text(encoding="utf-8")).get("ts") or 0)
    except (OSError, ValueError):
        last = 0
    if now - last < hours * 3600:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"ts": now}), encoding="utf-8")
    except OSError:
        pass
    return True


def stop_reminder(store, *, event: str = "Stop", block: bool | None = None) -> dict | None:
    """Stop: block once when a failure was fixed this session and never claimed.

    Gemini AfterAgent / SessionEnd must not use Claude's decision:block — they get
    additionalContext only.
    """
    from .env import last_failure, remember_failure

    if block is None:
        block = event == "Stop"
    rec = last_failure()
    if not rec or not rec.get("nudged") or rec.get("stop_nudged"):
        nudge = share_nudge(store)
        if nudge and _stop_share_nudge_due():
            return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "CLAIMIDX " + nudge}}
        return None
    rec["stop_nudged"] = True
    remember_failure(
        str(rec["err"]),
        command=str(rec.get("command") or ""),
        cwd=str(rec.get("cwd") or ""),
        eco=str(rec.get("eco") or ""),
        rt=str(rec.get("rt") or ""),
        event="fixed",
        fp=str(rec.get("fp") or ""),
        extra={"nudged": True, "stop_nudged": True},
    )
    reason = (
        f"You fixed `{rec.get('command') or 'a failing command'}` ({rec['err'][:100]}) but did not record it. "
        "Run `claimidx claim --yes` (or `claimidx claim` to review the draft), then stop."
    )
    if not block:
        return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": "CLAIMIDX " + reason}}
    return {
        "decision": "block",
        "reason": reason,
    }


def claude_context(event: str, dense: str) -> str:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": dense[:4000],
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def hook_command() -> str:
    """A command string cmd.exe, PowerShell, and POSIX sh can all invoke."""
    exe = sys.executable
    if os.name == "nt":
        return '"' + exe.replace('"', '""') + '" -m claimidx hook'
    return f"{shlex.quote(exe)} -m claimidx hook"


def claude_settings_path() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override) / "settings.json"
    return Path.home() / ".claude" / "settings.json"


# The four moments an agent forgets Claimidx, and the one command that covers them all:
#   PostToolUseFailure  a command failed          -> ask, remember the failure
#   PostToolUse         the same command passed   -> "you fixed it: claimidx claim --yes"
#   SessionStart        a new session             -> one-line brief (impact, what to do)
#   Stop                the turn is ending        -> once: a fixed-but-unclaimed failure
CLAUDE_EVENTS: tuple[tuple[str, str | None], ...] = (
    ("PostToolUseFailure", "Bash"),
    ("PostToolUse", "Bash"),
    ("SessionStart", None),
    ("Stop", None),
)


def claude_hook_block(matcher: str | None = "Bash") -> dict:
    h: dict = {"type": "command", "command": hook_command()}
    if os.name == "nt":
        h["shell"] = "powershell"
    block: dict = {"hooks": [h]}
    if matcher:
        block["matcher"] = matcher
    return block


def settings_has_claimidx(data: dict) -> bool:
    hooks = data.get("hooks") or {}
    for event, _matcher in CLAUDE_EVENTS:
        present = False
        for group in hooks.get(event) or []:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks") or []:
                if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                    present = True
        if not present:
            return False
    return True


def merge_claude_hooks(data: dict) -> dict:
    out = dict(data or {})
    raw_hooks = out.get("hooks")
    if raw_hooks is None:
        raw_hooks = {}
    if not isinstance(raw_hooks, dict):
        raise ValueError("settings.json hooks is not an object")
    hooks = dict(raw_hooks)
    cmd = hook_command()
    for event, matcher in CLAUDE_EVENTS:
        raw_groups = hooks.get(event) or []
        if not isinstance(raw_groups, list):
            raise ValueError(f"settings.json {event} is not a list")
        groups = list(raw_groups)
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
            groups.append(claude_hook_block(matcher))
        hooks[event] = groups
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
        "events": [e for e, _m in CLAUDE_EVENTS],
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


def cursor_hooks_path() -> Path:
    override = os.environ.get("CLAIMIDX_CURSOR_HOOKS")
    if override:
        return Path(override)
    return cursor_mcp_path().parent / "hooks.json"


def grok_config_path() -> Path:
    override = os.environ.get("CLAIMIDX_GROK_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".grok" / "config.toml"


def grok_hooks_path() -> Path:
    override = os.environ.get("CLAIMIDX_GROK_HOOKS")
    if override:
        return Path(override)
    return grok_config_path().parent / "hooks" / "claimidx.json"


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


def gemini_settings_path() -> Path:
    override = os.environ.get("CLAIMIDX_GEMINI_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".gemini" / "settings.json"


def codex_config_path() -> Path:
    override = os.environ.get("CLAIMIDX_CODEX_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".codex" / "config.toml"


def codex_hooks_path() -> Path:
    override = os.environ.get("CLAIMIDX_CODEX_HOOKS")
    if override:
        return Path(override)
    return codex_config_path().parent / "hooks.json"


def cline_mcp_path() -> Path:
    override = os.environ.get("CLAIMIDX_CLINE_MCP")
    if override:
        return Path(override)
    return Path.home() / ".cline" / "data" / "settings" / "cline_mcp_settings.json"


def continue_mcp_path() -> Path:
    override = os.environ.get("CLAIMIDX_CONTINUE_MCP")
    if override:
        return Path(override)
    return Path.home() / ".continue" / "mcpServers" / "claimidx.json"


def windsurf_mcp_path() -> Path:
    override = os.environ.get("CLAIMIDX_WINDSURF_MCP")
    if override:
        return Path(override)
    return Path.home() / ".codeium" / "windsurf" / "mcp_config.json"


def _json_mcp_block(own: str, agent: str) -> dict:
    return {"command": "claimidx-mcp", "args": [], "env": mcp_owner_env(own, agent)}


def _sync_json_owner(existing: dict, *, own: str, agent: str, env_key: str) -> bool:
    """Write CLAIMIDX_OWNER/AGENT into an existing MCP block. True if the file must be saved."""
    env = existing.get(env_key)
    if not isinstance(env, dict):
        env = {}
        existing[env_key] = env
    wanted = mcp_owner_env(own, agent)
    if all(env.get(k) == v for k, v in wanted.items()):
        return False
    env.update(wanted)
    return True


def _toml_sync_claimidx_owner(text: str, *, own: str, agent: str) -> tuple[str, bool]:
    """Replace CLAIMIDX_OWNER/AGENT under [mcp_servers.claimidx.env]. Leaves the rest of the file."""
    marker = "[mcp_servers.claimidx.env]"
    idx = text.find(marker)
    if idx < 0:
        return text, False
    rest_start = idx + len(marker)
    nxt = text.find("\n[", rest_start)
    if nxt < 0:
        section, tail = text[rest_start:], ""
    else:
        section, tail = text[rest_start:nxt], text[nxt:]
    head = text[:rest_start]
    wanted = {"CLAIMIDX_OWNER": own}
    if agent:
        wanted["CLAIMIDX_AGENT"] = agent
    changed = False
    for key, val in wanted.items():
        line = f"{key} = {_toml_str(val)}"
        m = re.search(rf"(?m)^{re.escape(key)}\s*=\s*.*$", section)
        if m:
            if m.group(0) != line:
                section = section[: m.start()] + line + section[m.end() :]
                changed = True
        else:
            if not section.endswith("\n"):
                section += "\n"
            section += line + "\n"
            changed = True
    return head + section + tail, changed


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
        if not _sync_json_owner(existing, own=own, agent=agent, env_key="env"):
            return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
    servers["claimidx"] = _json_mcp_block(own, agent)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def install_grok_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Append [mcp_servers.claimidx] to Grok config.toml if missing. Owner updates stay in that env table."""
    target = path or grok_config_path()
    forced = bool(os.environ.get("CLAIMIDX_GROK_CONFIG"))
    if not forced and not target.exists():
        return {"path": str(target), "status": "skip", "reason": "no grok config"}
    target.parent.mkdir(parents=True, exist_ok=True)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    if "[mcp_servers.claimidx]" in text:
        if "[mcp_servers.claimidx.env]" not in text:
            env_block = f"\n[mcp_servers.claimidx.env]\nCLAIMIDX_OWNER = {_toml_str(own)}\n"
            if agent:
                env_block += f"CLAIMIDX_AGENT = {_toml_str(agent)}\n"
            text = text.rstrip() + "\n" + env_block
            target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
            return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
        text, changed = _toml_sync_claimidx_owner(text, own=own, agent=agent)
        if not changed:
            return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
        target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
    block = f'\n[mcp_servers.claimidx]\ncommand = "claimidx-mcp"\n\n[mcp_servers.claimidx.env]\nCLAIMIDX_OWNER = {_toml_str(own)}\n'
    if agent:
        block += f"CLAIMIDX_AGENT = {_toml_str(agent)}\n"
    target.write_text((text.rstrip() + "\n" if text.strip() else "") + block.lstrip("\n"), encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def _load_json_object(target: Path, label: str) -> tuple[dict | None, dict | None]:
    """Return (data, None) or (None, error). A missing file is empty data."""
    if not target.exists():
        return {}, None
    try:
        loaded = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return None, {"path": str(target), "status": "error", "error": f"{label} is not json: {e}"}
    if not isinstance(loaded, dict):
        return None, {"path": str(target), "status": "error", "error": f"{label} is not a json object"}
    return loaded, None


def install_opencode_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Merge claimidx into OpenCode opencode.json. Skip if OpenCode is not installed."""
    target = path or opencode_config_path()
    forced = bool(os.environ.get("CLAIMIDX_OPENCODE_CONFIG"))
    if not forced and not target.exists() and not target.parent.exists():
        return {"path": str(target), "status": "skip", "reason": "no opencode config dir"}
    target.parent.mkdir(parents=True, exist_ok=True)
    data, err = _load_json_object(target, "opencode.json")
    if err or data is None:
        return err or {"path": str(target), "status": "error", "error": "unreadable"}
    mcp = data.get("mcp")
    if mcp is None:
        mcp = {}
        data["mcp"] = mcp
    if not isinstance(mcp, dict):
        return {"path": str(target), "status": "error", "error": "mcp is not an object"}
    existing = mcp.get("claimidx")
    if isinstance(existing, dict) and "claimidx-mcp" in str(existing.get("command") or ""):
        if not _sync_json_owner(existing, own=own, agent=agent, env_key="environment"):
            return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
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
    data, err = _load_json_object(target, "mcp.json")
    if err or data is None:
        return err or {"path": str(target), "status": "error", "error": "unreadable"}
    servers = data.get("servers")
    if servers is None:
        servers = {}
        data["servers"] = servers
    if not isinstance(servers, dict):
        return {"path": str(target), "status": "error", "error": "servers is not an object"}
    existing = servers.get("claimidx")
    if isinstance(existing, dict) and existing.get("command") == "claimidx-mcp":
        if not _sync_json_owner(existing, own=own, agent=agent, env_key="env"):
            return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
    servers["claimidx"] = _json_mcp_block(own, agent)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def _install_json_mcp(
    target: Path,
    *,
    own: str,
    agent: str = "",
    servers_key: str = "mcpServers",
    env_key: str = "env",
    skip_reason: str = "no config dir",
    forced: bool = False,
    present_home: Path | None = None,
    label: str = "mcp.json",
) -> dict:
    """Merge claimidx-mcp into a {servers_key: {claimidx: {command, env}}} JSON file."""
    if not forced and not target.exists() and not target.parent.exists() and not (present_home and present_home.exists()):
        return {"path": str(target), "status": "skip", "reason": skip_reason}
    target.parent.mkdir(parents=True, exist_ok=True)
    data, err = _load_json_object(target, label)
    if err:
        return err
    assert data is not None
    servers = data.get(servers_key)
    if servers is None:
        servers = {}
        data[servers_key] = servers
    if not isinstance(servers, dict):
        return {"path": str(target), "status": "error", "error": f"{servers_key} is not an object"}
    existing = servers.get("claimidx")
    if isinstance(existing, dict) and existing.get("command") == "claimidx-mcp":
        if not _sync_json_owner(existing, own=own, agent=agent, env_key=env_key):
            return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
    servers["claimidx"] = _json_mcp_block(own, agent)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


def install_gemini_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    target = path or gemini_settings_path()
    forced = bool(os.environ.get("CLAIMIDX_GEMINI_CONFIG")) or path is not None
    return _install_json_mcp(
        target,
        own=own,
        agent=agent,
        skip_reason="no gemini config",
        forced=forced,
        present_home=target.parent,
        label="settings.json",
    )


def install_cline_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    target = path or cline_mcp_path()
    forced = bool(os.environ.get("CLAIMIDX_CLINE_MCP")) or path is not None
    home = target.parents[2] if len(target.parents) >= 3 else target.parent
    return _install_json_mcp(
        target,
        own=own,
        agent=agent,
        skip_reason="no cline config",
        forced=forced,
        present_home=home,
        label="cline_mcp_settings.json",
    )


def install_windsurf_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    target = path or windsurf_mcp_path()
    forced = bool(os.environ.get("CLAIMIDX_WINDSURF_MCP")) or path is not None
    return _install_json_mcp(
        target,
        own=own,
        agent=agent,
        skip_reason="no windsurf config",
        forced=forced,
        present_home=target.parent,
        label="mcp_config.json",
    )


def install_continue_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Drop a Claude-shaped MCP JSON into Continue's mcpServers directory."""
    target = path or continue_mcp_path()
    forced = bool(os.environ.get("CLAIMIDX_CONTINUE_MCP")) or path is not None
    return _install_json_mcp(
        target,
        own=own,
        agent=agent,
        skip_reason="no continue config",
        forced=forced,
        present_home=target.parent.parent,
        label="claimidx.json",
    )


def install_codex_mcp(path: Path | None = None, *, own: str, agent: str = "") -> dict:
    """Append [mcp_servers.claimidx] to ~/.codex/config.toml when Codex is present."""
    target = path or codex_config_path()
    forced = bool(os.environ.get("CLAIMIDX_CODEX_CONFIG")) or path is not None
    if not forced and not target.exists() and not target.parent.exists():
        return {"path": str(target), "status": "skip", "reason": "no codex config"}
    target.parent.mkdir(parents=True, exist_ok=True)
    text = target.read_text(encoding="utf-8") if target.exists() else ""
    if "[mcp_servers.claimidx]" in text:
        if "[mcp_servers.claimidx.env]" not in text:
            env_block = f"\n[mcp_servers.claimidx.env]\nCLAIMIDX_OWNER = {_toml_str(own)}\n"
            if agent:
                env_block += f"CLAIMIDX_AGENT = {_toml_str(agent)}\n"
            text = text.rstrip() + "\n" + env_block
            target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
            return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
        text, changed = _toml_sync_claimidx_owner(text, own=own, agent=agent)
        if not changed:
            return {"path": str(target), "status": "present", "command": "claimidx-mcp"}
        target.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
        return {"path": str(target), "status": "updated", "command": "claimidx-mcp"}
    block = f'\n[mcp_servers.claimidx]\ncommand = "claimidx-mcp"\n\n[mcp_servers.claimidx.env]\nCLAIMIDX_OWNER = {_toml_str(own)}\n'
    if agent:
        block += f"CLAIMIDX_AGENT = {_toml_str(agent)}\n"
    target.write_text((text.rstrip() + "\n" if text.strip() else "") + block.lstrip("\n"), encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": "claimidx-mcp"}


CODEX_EVENTS: tuple[tuple[str, str | None], ...] = (
    ("PostToolUseFailure", "Bash|shell"),
    ("PostToolUse", "Bash|shell"),
    ("SessionStart", None),
    ("Stop", None),
)

GEMINI_EVENTS: tuple[tuple[str, str | None], ...] = (
    ("AfterTool", "run_shell_command|shell"),
    ("SessionStart", None),
    ("AfterAgent", None),
)


def _grouped_hook_block(matcher: str | None, *, extra: dict | None = None, windows_shell: bool = False) -> dict:
    h: dict[str, Any] = {"type": "command", "command": hook_command(), **(extra or {})}
    if windows_shell and os.name == "nt" and "shell" not in h:
        h["shell"] = "powershell"
    block: dict[str, Any] = {"hooks": [h]}
    if matcher:
        block["matcher"] = matcher
    return block


def merge_grouped_hooks(
    data: dict,
    events: tuple[tuple[str, str | None], ...],
    *,
    extra: dict | None = None,
    windows_shell: bool = False,
) -> dict:
    out = dict(data or {})
    raw_hooks = out.get("hooks")
    if raw_hooks is None:
        raw_hooks = {}
    if not isinstance(raw_hooks, dict):
        raise ValueError("hooks is not an object")
    hooks = dict(raw_hooks)
    cmd = hook_command()
    for event, matcher in events:
        raw_groups = hooks.get(event) or []
        if not isinstance(raw_groups, list):
            raise ValueError(f"{event} is not a list")
        groups = list(raw_groups)
        found = False
        for group in groups:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks") or []:
                if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                    h.clear()
                    h.update(_grouped_hook_block(matcher, extra=extra, windows_shell=windows_shell)["hooks"][0])
                    h["command"] = cmd
                    if matcher:
                        group["matcher"] = matcher
                    else:
                        group.pop("matcher", None)
                    found = True
        if not found:
            groups.append(_grouped_hook_block(matcher, extra=extra, windows_shell=windows_shell))
        hooks[event] = groups
    out["hooks"] = hooks
    return out


def grouped_hooks_has_claimidx(data: dict, events: tuple[tuple[str, str | None], ...]) -> bool:
    hooks = data.get("hooks") or {}
    if not isinstance(hooks, dict):
        return False
    for event, _matcher in events:
        found = False
        for group in hooks.get(event) or []:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks") or []:
                if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                    found = True
        if not found:
            return False
    return True


def grouped_hooks_current(data: dict, events: tuple[tuple[str, str | None], ...], cmd: str) -> bool:
    """True when every event has our hook, the current command, and the wanted matcher."""
    if not grouped_hooks_has_claimidx(data, events):
        return False
    hooks = data.get("hooks") or {}
    for event, matcher in events:
        found_cmd = None
        found_matcher: str | None = None
        for group in hooks.get(event) or []:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks") or []:
                if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                    found_cmd = str(h.get("command") or "")
                    found_matcher = group.get("matcher")
        if found_cmd != cmd or found_matcher != matcher:
            return False
    return True


def install_codex_hooks(path: Path | None = None) -> dict:
    """Merge the sensor into ~/.codex/hooks.json when Codex is present."""
    target = path or codex_hooks_path()
    forced = bool(os.environ.get("CLAIMIDX_CODEX_HOOKS")) or bool(os.environ.get("CLAIMIDX_CODEX_CONFIG")) or path is not None
    cfg = codex_config_path()
    if not forced and not cfg.exists() and not cfg.parent.exists() and not target.exists():
        return {"path": str(target), "status": "skip", "reason": "no codex config"}
    target.parent.mkdir(parents=True, exist_ok=True)
    data, err = _load_json_object(target, "hooks.json")
    if err:
        return err
    assert data is not None
    cmd = hook_command()
    if grouped_hooks_current(data, CODEX_EVENTS, cmd):
        return {"path": str(target), "status": "present", "command": cmd}
    try:
        merged = merge_grouped_hooks(data, CODEX_EVENTS, extra={"timeout": 15})
    except ValueError as e:
        return {"path": str(target), "status": "error", "error": str(e)}
    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": cmd, "events": [e for e, _m in CODEX_EVENTS]}


def install_gemini_hooks(path: Path | None = None) -> dict:
    """Merge AfterTool / SessionStart / AfterAgent into ~/.gemini/settings.json."""
    target = path or gemini_settings_path()
    forced = bool(os.environ.get("CLAIMIDX_GEMINI_CONFIG")) or path is not None
    if not forced and not target.exists() and not target.parent.exists():
        return {"path": str(target), "status": "skip", "reason": "no gemini config"}
    target.parent.mkdir(parents=True, exist_ok=True)
    data, err = _load_json_object(target, "settings.json")
    if err:
        return err
    assert data is not None
    cmd = hook_command()
    extra = {"name": "claimidx", "timeout": 15000}
    if grouped_hooks_current(data, GEMINI_EVENTS, cmd):
        after = ((data.get("hooks") or {}).get("AfterTool") or [{}])[0]
        handlers = after.get("hooks") if isinstance(after, dict) else None
        first = handlers[0] if isinstance(handlers, list) and handlers else {}
        if isinstance(first, dict) and "shell" not in first:
            return {"path": str(target), "status": "present", "command": cmd}
    try:
        merged = merge_grouped_hooks(data, GEMINI_EVENTS, extra=extra, windows_shell=False)
    except ValueError as e:
        return {"path": str(target), "status": "error", "error": str(e)}
    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": cmd, "events": [e for e, _m in GEMINI_EVENTS]}


def grok_hook_file(cmd: str | None = None) -> dict:
    """Native Grok hooks file. Matcher includes run_terminal_command; timeout stays short."""
    h: dict[str, Any] = {"type": "command", "command": cmd or hook_command(), "timeout": 15}
    matcher = "Bash|run_terminal_command"
    events: dict[str, list] = {}
    for event, use_matcher in (
        ("PostToolUseFailure", True),
        ("PostToolUse", True),
        ("SessionStart", False),
        ("Stop", False),
    ):
        group: dict[str, Any] = {"hooks": [dict(h)]}
        if use_matcher:
            group["matcher"] = matcher
        events[event] = [group]
    return {"hooks": events}


def grok_hooks_has_claimidx(data: dict) -> bool:
    hooks = data.get("hooks") or {}
    if not isinstance(hooks, dict):
        return False
    for event, _matcher in CLAUDE_EVENTS:
        found = False
        for group in hooks.get(event) or []:
            if not isinstance(group, dict):
                continue
            for h in group.get("hooks") or []:
                if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                    found = True
        if not found:
            return False
    return True


def install_grok_hooks(path: Path | None = None) -> dict:
    """Write ~/.grok/hooks/claimidx.json so Grok sees the sensor without Claude compat."""
    target = path or grok_hooks_path()
    forced = bool(os.environ.get("CLAIMIDX_GROK_HOOKS")) or path is not None
    cfg = grok_config_path()
    if not forced and not cfg.exists() and not target.parent.exists():
        return {"path": str(target), "status": "skip", "reason": "no grok config"}
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = hook_command()
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return {"path": str(target), "status": "error", "error": f"claimidx.json is not json: {e}"}
        if isinstance(loaded, dict) and grok_hooks_has_claimidx(loaded):
            existing_cmd = ""
            for group in (loaded.get("hooks") or {}).get("PostToolUse") or []:
                if not isinstance(group, dict):
                    continue
                for h in group.get("hooks") or []:
                    if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                        existing_cmd = str(h.get("command") or "")
            if existing_cmd == cmd:
                return {"path": str(target), "status": "present", "command": cmd}
    payload = grok_hook_file(cmd)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": cmd, "events": [e for e, _m in CLAUDE_EVENTS]}


def cursor_hook_file(cmd: str | None = None) -> dict:
    """Cursor ~/.cursor/hooks.json (version 1, camelCase events, command at the entry)."""
    h = {"command": cmd or hook_command(), "timeout": 15}
    stop = dict(h)
    stop["loop_limit"] = 1
    return {
        "version": 1,
        "hooks": {
            "afterShellExecution": [dict(h)],
            "postToolUseFailure": [dict(h)],
            "postToolUse": [dict(h)],
            "sessionStart": [dict(h)],
            "stop": [stop],
        },
    }


def cursor_hooks_has_claimidx(data: dict) -> bool:
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for event in ("afterShellExecution", "postToolUseFailure", "postToolUse", "sessionStart", "stop"):
        found = False
        for h in hooks.get(event) or []:
            if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                found = True
        if not found:
            return False
    return True


def install_cursor_hooks(path: Path | None = None) -> dict:
    """Merge the sensor into ~/.cursor/hooks.json when Cursor is present."""
    target = path or cursor_hooks_path()
    forced = bool(os.environ.get("CLAIMIDX_CURSOR_HOOKS")) or path is not None
    mcp = cursor_mcp_path()
    if not forced and not mcp.exists() and not mcp.parent.exists() and not target.exists():
        return {"path": str(target), "status": "skip", "reason": "no cursor config dir"}
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = hook_command()
    data, err = _load_json_object(target, "hooks.json")
    if err:
        return err
    assert data is not None
    if data.get("version") not in (None, 1):
        return {"path": str(target), "status": "error", "error": "hooks.json version is not 1"}
    payload = cursor_hook_file(cmd)
    if cursor_hooks_has_claimidx(data):
        existing_cmd = ""
        for h in (data.get("hooks") or {}).get("afterShellExecution") or []:
            if isinstance(h, dict) and _MARKER in str(h.get("command") or ""):
                existing_cmd = str(h.get("command") or "")
        if existing_cmd == cmd:
            return {"path": str(target), "status": "present", "command": cmd}
    # Merge our events into an existing file without dropping other hooks.
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
        data["hooks"] = hooks
    data["version"] = 1
    for event, groups in payload["hooks"].items():
        raw = hooks.get(event) or []
        if not isinstance(raw, list):
            raw = []
        kept = [h for h in raw if not (isinstance(h, dict) and _MARKER in str(h.get("command") or ""))]
        hooks[event] = groups + kept
        # ours first so a later user hook cannot hide the sensor
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(target), "status": "installed", "command": cmd}


def bundled_skill_text() -> str | None:
    """SKILL.md from the checkout or the wheel. None if this install has no copy."""
    here = Path(__file__).resolve().parent
    for path in (
        here.parents[2] / "skills" / "claimidx" / "SKILL.md",
        here / "data" / "SKILL.md",
    ):
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                continue
    return None


def _skill_ours(text: str) -> bool:
    return bool(re.search(r"(?m)^name:\s*claimidx\s*$", text))


def install_one_skill(target: Path, body: str) -> dict:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        try:
            current = target.read_text(encoding="utf-8")
        except OSError as e:
            return {"path": str(target), "status": "error", "error": str(e)}
        if current == body:
            return {"path": str(target), "status": "present"}
        if current and not _skill_ours(current):
            return {"path": str(target), "status": "skip", "reason": "not a claimidx skill"}
    target.write_text(body, encoding="utf-8")
    return {"path": str(target), "status": "installed"}


def install_user_skills() -> dict:
    """Drop SKILL.md into user harness skill dirs so other trees see Claimidx."""
    body = bundled_skill_text()
    if not body:
        return {"status": "skip", "reason": "no bundled skill"}
    out: dict[str, Any] = {}
    targets = {
        "claude": claude_settings_path().parent / "skills" / "claimidx" / "SKILL.md",
        "grok": grok_config_path().parent / "skills" / "claimidx" / "SKILL.md",
        "cursor": cursor_mcp_path().parent / "skills" / "claimidx" / "SKILL.md",
        "codex": codex_config_path().parent / "skills" / "claimidx" / "SKILL.md",
        "gemini": gemini_settings_path().parent / "skills" / "claimidx" / "SKILL.md",
        "opencode": opencode_config_path().parent / "skills" / "claimidx" / "SKILL.md",
        "cline": (cline_mcp_path().parents[2] if len(cline_mcp_path().parents) >= 3 else cline_mcp_path().parent) / "skills" / "claimidx" / "SKILL.md",
        "continue": continue_mcp_path().parent.parent / "skills" / "claimidx" / "SKILL.md",
        "windsurf": windsurf_mcp_path().parent / "skills" / "claimidx" / "SKILL.md",
    }
    required = {
        "claude": True,  # init always writes Claude settings
        "grok": grok_config_path().exists() or grok_config_path().parent.exists(),
        "cursor": cursor_mcp_path().exists() or cursor_mcp_path().parent.exists(),
        "codex": codex_config_path().exists() or codex_config_path().parent.exists(),
        "gemini": gemini_settings_path().exists() or gemini_settings_path().parent.exists(),
        "opencode": opencode_config_path().exists() or opencode_config_path().parent.exists(),
        "cline": cline_mcp_path().exists()
        or bool(os.environ.get("CLAIMIDX_CLINE_MCP"))
        or (len(cline_mcp_path().parents) >= 3 and cline_mcp_path().parents[2].exists()),
        "continue": continue_mcp_path().exists() or bool(os.environ.get("CLAIMIDX_CONTINUE_MCP")) or continue_mcp_path().parent.parent.exists(),
        "windsurf": windsurf_mcp_path().exists() or windsurf_mcp_path().parent.exists(),
    }
    for name, path in targets.items():
        if not required[name]:
            out[name] = {"path": str(path), "status": "skip", "reason": f"no {name} config"}
            continue
        out[name] = install_one_skill(path, body)
    return out


def install_harness(*, own: str, agent: str = "") -> dict:
    """Sensors, skill drop, and MCP for every harness already on this machine."""
    return {
        "claude": install_claude_hook(),
        "cursor": install_cursor_mcp(own=own, agent=agent),
        "cursor_hooks": install_cursor_hooks(),
        "grok": install_grok_mcp(own=own, agent=agent),
        "grok_hooks": install_grok_hooks(),
        "opencode": install_opencode_mcp(own=own, agent=agent),
        "vscode": install_vscode_mcp(own=own, agent=agent),
        "codex": install_codex_mcp(own=own, agent=agent),
        "codex_hooks": install_codex_hooks(),
        "gemini": install_gemini_mcp(own=own, agent=agent),
        "gemini_hooks": install_gemini_hooks(),
        "cline": install_cline_mcp(own=own, agent=agent),
        "continue": install_continue_mcp(own=own, agent=agent),
        "windsurf": install_windsurf_mcp(own=own, agent=agent),
        "skills": install_user_skills(),
    }
