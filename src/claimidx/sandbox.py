"""Opt-in eval replay. Never used on pull or publish."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .policy import eval_allowed, _norm_head, _prep_eval


def resolve_argv(parts: list[str]) -> list[str]:
    """Map an allowlisted eval recipe onto this OS's real executable.

    Unix recipes stay canonical (`python -c`, `npx tsc`). On Windows we
    resolve `python` to `sys.executable` and `npx`/`npm`/`node` via PATHEXT.
    """
    if not parts:
        return parts
    name = _norm_head(parts[0])
    if name in ("python", "python3"):
        return [sys.executable, *parts[1:]]
    found = _which(parts[0])
    return [found or parts[0], *parts[1:]]


def _which(head: str) -> str | None:
    names = [head]
    if os.name == "nt":
        base = _norm_head(head)
        names.extend([base, f"{base}.exe", f"{base}.cmd", f"{base}.bat"])
    seen: set[str] = set()
    for n in names:
        key = n.lower()
        if key in seen:
            continue
        seen.add(key)
        found = shutil.which(n)
        if found:
            return found
    return None


@dataclass
class ReplayResult:
    allowed: bool
    ran: bool
    rc: int | None
    expect: int
    held: bool
    reason: str
    stdout: str = ""
    stderr: str = ""

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "ran": self.ran,
            "rc": self.rc,
            "expect": self.expect,
            "held": self.held,
            "reason": self.reason,
            "stdout": self.stdout[-400:],
            "stderr": self.stderr[-400:],
        }


def replay(cmd: str, expect: int = 0, timeout: float = 45.0) -> ReplayResult:
    ok, reason = eval_allowed(cmd)
    if not ok:
        return ReplayResult(False, False, None, expect, False, reason)
    try:
        parts = shlex.split(_prep_eval(cmd))
    except ValueError as e:
        return ReplayResult(False, False, None, expect, False, f"unparseable eval: {e}")
    if parts and _norm_head(parts[0]) in ("true", "false"):
        rc = 0 if _norm_head(parts[0]) == "true" else 1
        held = rc == expect
        return ReplayResult(True, True, rc, expect, held, "builtin")
    argv = resolve_argv(parts)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return ReplayResult(True, True, 124, expect, False, "timeout")
    except OSError as e:
        return ReplayResult(True, False, None, expect, False, f"exec-error:{e}")
    held = proc.returncode == expect
    return ReplayResult(
        True, True, proc.returncode, expect, held,
        "held" if held else "eval-miss",
        proc.stdout or "", proc.stderr or "",
    )
