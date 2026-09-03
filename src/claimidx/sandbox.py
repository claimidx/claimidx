"""Opt-in eval replay. Never used on pull or publish."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass

from .fingerprint import runtime_proof_key
from .policy import _norm_head, eval_allowed, split_eval


def resolve_argv(parts: list[str]) -> list[str]:
    """Map an allowlisted eval recipe onto this OS's real executable.

    Unix recipes stay canonical (`python -c`, `npx tsc`). `python`/`python3`
    follow PATH (venv) or CLAIMIDX_PYTHON, then this interpreter. Other
    heads resolve via PATHEXT on Windows.
    """
    if not parts:
        return parts
    name = _norm_head(parts[0])
    if name in ("python", "python3"):
        pinned = (os.environ.get("CLAIMIDX_PYTHON") or "").strip()
        if pinned:
            return [pinned, *parts[1:]]
        found = _which(parts[0])
        if found:
            return [found, *parts[1:]]
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
    env: str = ""  # observed executing runtime, e.g. py@3.12 / node@20
    ms: int = 0

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
            "env": self.env,
            "ms": int(self.ms or 0),
        }

    def is_hint(self) -> bool:
        """true/false builtins, version tautologies, and unmet trees cannot mint nc."""
        r = self.reason or ""
        return r == "builtin" or r.startswith("eval-precondition")


_TREE_MARKERS = {
    "npx": ("package.json", "tsconfig.json"),
    "npm": ("package.json",),
    "go": ("go.mod", "go.work"),
    "cargo": ("Cargo.toml",),
    "rustc": ("Cargo.toml",),
    "docker": ("Dockerfile", "docker-compose.yml", "compose.yml"),
    "pytest": ("pytest.ini", "pyproject.toml", "setup.cfg", "tests", "test", "conftest.py"),
    "mvn": ("pom.xml",),
    "gradle": ("build.gradle", "build.gradle.kts"),
    "composer": ("composer.json",),
    "bundle": ("Gemfile",),
    "bundler": ("Gemfile",),
    "gem": ("Gemfile",),
    "php": ("composer.json",),
    "make": ("Makefile", "makefile"),
}
_LOCAL_PIP = re.compile(r"\bpip\b.+\binstall\b.*(\s-e\s|\s\.(?:\s|$))", re.I)
_ENV_HEADS = {"python", "python3", "node"}


def observe_env(argv: list[str]) -> str:
    """Executing runtime of a resolved eval argv. Empty if not python/node."""
    if not argv:
        return ""
    exe = argv[0]
    base = os.path.basename(exe).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base in {"python", "python3", "pythonw"}:
        return _observe_py(exe)
    if base == "node":
        return _observe_node(exe)
    return ""


def _observe_py(exe: str) -> str:
    try:
        if os.path.exists(exe) and os.path.exists(sys.executable) and os.path.samefile(exe, sys.executable):
            v = sys.version_info
            return f"py@{v.major}.{v.minor}"
    except OSError:
        pass
    try:
        proc = subprocess.run(
            [exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    ver = (proc.stdout or "").strip()
    if re.fullmatch(r"\d+\.\d+", ver):
        return f"py@{ver}"
    return ""


def _observe_node(exe: str) -> str:
    try:
        proc = subprocess.run(
            [exe, "-p", "process.versions.node"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    ver = (proc.stdout or "").strip()
    m = re.match(r"(\d+)", ver)
    if m:
        return f"node@{m.group(1)}"
    return ""


def _eval_needs_env(cmd: str) -> bool:
    try:
        _, parts = split_eval(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    return _norm_head(parts[0]) in _ENV_HEADS


def replay_records_hold(claim_rt: str, result: ReplayResult, cmd: str = "") -> tuple[bool, str]:
    """Whether a held replay may mint nr.

    Python/node evals observe the executing interpreter. That env is
    required: claim.rt must be set and match at runtime_proof_key grain.
    Other heads keep prior confirm-replay behavior.
    """
    if result.is_hint() or not result.held:
        return False, result.reason
    if not _eval_needs_env(cmd):
        return True, "held"
    observed = (result.env or "").strip()
    if not observed:
        return False, "hold requires observed env"
    declared = (claim_rt or "").strip()
    if not declared:
        return False, f"hold requires rt matching observed env ({observed})"
    if runtime_proof_key(declared) != runtime_proof_key(observed):
        return False, f"hold env mismatch: claim.rt={declared} observed={observed}"
    return True, "held"


def _precondition(head: str, cwd: str | None, cmd: str = "") -> str | None:
    markers = _TREE_MARKERS.get(head)
    blob = cmd or ""
    if head in {"python", "python3"} and _LOCAL_PIP.search(blob):
        markers = ("pyproject.toml", "setup.py", "setup.cfg")
    elif head in {"python", "python3"} and re.search(r"\bpytest\b", blob):
        markers = _TREE_MARKERS["pytest"]
    elif re.search(r"\bmake\b", blob) and head in {"python", "python3", "node", "test", "make"}:
        markers = _TREE_MARKERS["make"]
    if not markers:
        return None
    root = cwd or os.getcwd()
    if any(os.path.exists(os.path.join(root, name)) for name in markers):
        return None
    return f"eval-precondition: no {'/'.join(markers)} in cwd"


def replay(cmd: str, expect: int = 0, timeout: float = 45.0, cwd: str | None = None) -> ReplayResult:
    if not (cmd or "").strip():
        return ReplayResult(True, False, None, expect, False, "builtin")
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
    from .public import eval_is_proof

    if not eval_is_proof(cmd):
        return ReplayResult(True, False, None, expect, False, "builtin")
    missing = _precondition(_norm_head(parts[0]), cwd, cmd)
    if missing:
        return ReplayResult(True, False, None, expect, False, missing)
    argv = resolve_argv(parts)
    observed = observe_env(argv)
    env = os.environ.copy()
    env.update(extra_env)
    t0 = time.monotonic()
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
        ms = int((time.monotonic() - t0) * 1000)
        return ReplayResult(True, True, 124, expect, False, "timeout", env=observed, ms=ms)
    except OSError as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ReplayResult(True, False, None, expect, False, f"exec-error:{e}", env=observed, ms=ms)
    held = proc.returncode == expect
    ms = int((time.monotonic() - t0) * 1000)
    return ReplayResult(
        True,
        True,
        proc.returncode,
        expect,
        held,
        "held" if held else "eval-miss",
        proc.stdout or "",
        proc.stderr or "",
        observed,
        ms,
    )
