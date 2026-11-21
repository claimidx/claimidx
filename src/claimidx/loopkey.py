"""Persistent retry/loop key: first error line + command + exit code.

Local only. The key is stable across processes and is not tied to session_id.
Inputs are redacted before they are hashed or stored.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from typing import Any

UNKEYABLE = "unkeyable"
DEFAULT_LOOP_LIMIT = 3

_ISO_TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_EPOCH = re.compile(r"\b(?:1[0-9]{9}|2[0-9]{9})(?:\.\d+)?\b")
_TEMP = re.compile(
    r"(?i)(?:(?:/var)?/tmp/|/var/folders/|%TEMP%\\|%TMP%\\|\$TMPDIR/"
    r"|C:\\Users\\[^\\]+\\AppData\\Local\\Temp\\)\S+"
)
_PID = re.compile(r"(?i)\b(?:pid[=: ]+)\d+\b")
_TIMEOUT_TAIL = re.compile(r"(?i)\b(?:timed out|timeout|deadline exceeded)\b")


def last_nonempty_line(output: str | None) -> str:
    for line in reversed((output or "").splitlines()):
        s = line.strip()
        if s:
            return s[:280]
    return ""


def scrub_line(text: str | None) -> str:
    """Redact, drop volatile tokens, then reuse normalize_error."""
    from .fingerprint import normalize_error
    from .security import redact

    s = redact(text or "")
    s = _ISO_TS.sub("<TS>", s)
    s = _TEMP.sub("<TMP>", s)
    s = _PID.sub("<PID>", s)
    s = _EPOCH.sub("<TS>", s)
    return normalize_error(s)


def loop_key(first_err: str | None, command: str | None, rc: int | None, last_line: str | None) -> str:
    """sha256 of the scrubbed first error line, command, exit code, and last non-empty line."""
    material = "\n".join(
        [
            scrub_line(first_err or ""),
            scrub_line(" ".join((command or "").split())),
            str(int(rc) if rc is not None else 0),
            scrub_line(last_line or ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def loop_task(cwd: str | None = None) -> str:
    """Scope a loop entry: absolute cwd, plus CLAIMIDX_TASK or CLAIMIDX_SESSION when set."""
    root = os.path.abspath(cwd or os.getcwd())
    extra = (os.environ.get("CLAIMIDX_TASK") or os.environ.get("CLAIMIDX_SESSION") or "").strip()
    return f"{root}\n{extra}"[:1000] if extra else root[:1000]


def loop_limit() -> int:
    """Refuse when this many identical tries would run. 0 disables the hard stop. Default 3."""
    raw = (os.environ.get("CLAIMIDX_LOOP_LIMIT") or "").strip()
    if not raw:
        return DEFAULT_LOOP_LIMIT
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_LOOP_LIMIT
    return max(0, n)


def run_raw_requested() -> bool:
    raw = (os.environ.get("CLAIMIDX_RUN_RAW") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_unkeyable_timeout(rc: int, output: str | None, err: str | None) -> bool:
    """Wrapper timeout (rc 124), or a timeout tail with no extractable error line."""
    if rc == 124:
        return True
    last = last_nonempty_line(output)
    return bool(last and _TIMEOUT_TAIL.search(last) and not (err or "").strip())


def artifact_digest(command: str | None, cwd: str | None) -> str:
    """Tree digest for reset/close: binding hashes when present, else git HEAD + diff --stat."""
    if not cwd:
        return ""
    try:
        from .binding import compute_binding

        binding = compute_binding(command or "", cwd, source="loop")
        if binding is not None:
            arts = getattr(binding, "artifacts", None) or []
            if arts:
                parts = [f"{a.path}:{a.hex}" for a in arts]
                return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    except Exception:
        pass
    return _git_artifact(cwd)


def _git_artifact(cwd: str) -> str:
    try:
        head = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if head.returncode != 0:
            return ""
        diff = subprocess.run(
            ["git", "-C", cwd, "diff", "--stat", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        blob = (head.stdout or "") + "\n" + (diff.stdout or "")
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def public_loop(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    """MCP/CLI shape: key, tries, stop, plus state and reset_reason when present."""
    if not entry:
        return None
    out: dict[str, Any] = {
        "key": str(entry.get("key") or ""),
        "tries": int(entry.get("tries") or 0),
        "stop": bool(entry.get("stop")),
    }
    state = str(entry.get("state") or "")
    if state:
        out["state"] = state
    reason = str(entry.get("reset_reason") or "")
    if reason:
        out["reset_reason"] = reason
    return out


def short_key(key: str | None) -> str:
    k = key or ""
    if k == UNKEYABLE or len(k) <= 8:
        return k
    return k[:8]


def format_try_line(entry: dict[str, Any]) -> str:
    if (entry.get("state") or "") == UNKEYABLE or (entry.get("key") or "") == UNKEYABLE:
        reason = entry.get("reset_reason") or "timeout"
        return f"CLAIMIDX loop unkeyable reason={reason}"
    limit = loop_limit() or DEFAULT_LOOP_LIMIT
    line = f"CLAIMIDX loop try {int(entry.get('tries') or 0)}/{limit} key={short_key(str(entry.get('key') or ''))}"
    reason = str(entry.get("reset_reason") or "")
    if reason and str(entry.get("event") or "") == "reset":
        line += f" reset={reason}"
    elif reason and int(entry.get("tries") or 0) == 1:
        line += f" reset={reason}"
    return line


def format_stop_line(entry: dict[str, Any]) -> str:
    return f"CLAIMIDX loop stop: ask/claim before retrying (key={short_key(str(entry.get('key') or ''))} tries={int(entry.get('tries') or 0)})"


def format_close_line(entry: dict[str, Any]) -> str:
    reason = str(entry.get("reset_reason") or "artifact-changed")
    return f"CLAIMIDX loop closed key={short_key(str(entry.get('key') or ''))} tries={int(entry.get('tries') or 0)} reason={reason}"


def close_line_visible(entry: dict[str, Any]) -> bool:
    """Print the close line only after a second try (tries>=2).

    A single failure that then passes closes the loop in the store and on
    MCP claimidx_session.loop, but stays off stderr so a later quiet pass
    (claimidx run after apply) has no CLAIMIDX output.
    """
    return int(entry.get("tries") or 0) >= 2


def artifact_allows_retry(entry: dict[str, Any], current_artifact: str) -> bool:
    """True when the tree changed enough that the hard stop should not fire."""
    stored = str(entry.get("artifact") or "")
    current = current_artifact or ""
    if current and current != stored:
        return True
    return False


def refuse_entry(store, *, command: str, cwd: str | None) -> dict[str, Any] | None:
    """Open loop entry that should block the next identical `claimidx run`, or None."""
    try:
        if not loop_limit():
            return None
        task = loop_task(cwd)
        entry = store.loop_get_for_command(command, task)
        if not entry or entry.get("state") != "open":
            return None
        art = artifact_digest(command, cwd)
        if artifact_allows_retry(entry, art):
            return None
        if int(entry.get("tries") or 0) >= max(1, loop_limit() - 1):
            return entry
        return None
    except Exception:
        return None


def apply_failure(
    store,
    *,
    command: str,
    rc: int,
    output: str | None,
    cwd: str | None,
    fp: str = "",
) -> dict[str, Any] | None:
    """Record a non-zero exit in loop_entries. Timeouts and spawn failures are unkeyable."""
    try:
        from .hook import _first_err_line
        from .security import redact, redact_credentials

        cmd = redact_credentials(" ".join((command or "").split()))
        text = redact(output or "")
        err = _first_err_line(text) or ""
        last = last_nonempty_line(text)
        task = loop_task(cwd)
        art = artifact_digest(cmd, cwd)
        if rc == 127 and not text:
            return store.loop_bump(UNKEYABLE, task=task, command=cmd, artifact=art, unkeyable=True, reason="spawn")
        if is_unkeyable_timeout(rc, text, err):
            return store.loop_bump(UNKEYABLE, task=task, command=cmd, artifact=art, unkeyable=True, reason="timeout")
        key = loop_key(err or last, cmd, rc, last)
        return store.loop_bump(key, task=task, fp=fp or "", command=cmd, artifact=art)
    except Exception:
        return None


def apply_success(store, *, command: str, cwd: str | None) -> dict[str, Any] | None:
    """Close the command's open entry when the artifact digest changed."""
    try:
        from .security import redact_credentials

        cmd = redact_credentials(" ".join((command or "").split()))
        task = loop_task(cwd)
        entry = store.loop_get_for_command(cmd, task)
        if not entry or entry.get("state") != "open":
            return None
        art = artifact_digest(cmd, cwd)
        if not art or art == str(entry.get("artifact") or ""):
            return None
        return store.loop_close(entry["key"], task=task, artifact=art, reason="artifact-changed")
    except Exception:
        return None
