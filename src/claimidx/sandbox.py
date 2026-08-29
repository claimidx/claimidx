"""Opt-in eval replay. Never used on pull or publish."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .policy import eval_allowed, split_eval, _norm_head


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


_TREE_MARKERS = {
    "npx": ("package.json", "tsconfig.json"),
    "npm": ("package.json",),
    "go": ("go.mod", "go.work"),
    "cargo": ("Cargo.toml",),
    "rustc": ("Cargo.toml",),
    "docker": ("Dockerfile", "docker-compose.yml", "compose.yml"),
}
_LOCAL_PIP = re.compile(r"\bpip\b.+\binstall\b.*(\s-e\s|\s\.(?:\s|$))", re.I)


def _precondition(head: str, cwd: str | None, cmd: str = "") -> str | None:
    markers = _TREE_MARKERS.get(head)
    if head in {"python", "python3"} and _LOCAL_PIP.search(cmd or ""):
        markers = ("pyproject.toml", "setup.py", "setup.cfg")
    if not markers:
        return None
    root = cwd or os.getcwd()
    if any(os.path.exists(os.path.join(root, name)) for name in markers):
        return None
    return f"eval-precondition: no {'/'.join(markers)} in cwd"


def replay(cmd: str, expect: int = 0, timeout: float = 45.0, cwd: str | None = None) -> ReplayResult:
    ok, reason = eval_allowed(cmd)
    if not ok:
        return ReplayResult(False, False, None, expect, False, reason)
    try:
        extra_env, parts = split_eval(cmd)
    except ValueError as e:
        return ReplayResult(False, False, None, expect, False, f"unparseable eval: {e}")
    if parts and _norm_head(parts[0]) in ("true", "false"):
        rc = 0 if _norm_head(parts[0]) == "true" else 1
        held = rc == expect
        return ReplayResult(True, True, rc, expect, held, "builtin")
    missing = _precondition(_norm_head(parts[0]), cwd, cmd)
    if missing:
        return ReplayResult(True, False, None, expect, False, missing)
    argv = resolve_argv(parts)
    env = os.environ.copy()
    env.update(extra_env)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            cwd=cwd or None,
            env=env,
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
