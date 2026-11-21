"""Compare two command outputs for stale identical results (item 9)."""

from __future__ import annotations

import subprocess
from typing import Any

from .fingerprint import normalize_error

SEP = ":::"
EMPTY = "empty-result"
IDENTICAL = "stale_identical"
DIFFERENT = "distinct"


def split_argv(argv: list[str]) -> tuple[list[str], list[str]] | None:
    """Split `cmd A ::: cmd B`. Leading `--` is ignored. None if `:::` is missing."""
    tokens = list(argv or [])
    if tokens and tokens[0] == "--":
        tokens = tokens[1:]
    if SEP not in tokens:
        return None
    i = tokens.index(SEP)
    left, right = tokens[:i], tokens[i + 1 :]
    if not left or not right:
        return None
    return left, right


def _run(argv: list[str], *, cwd: str | None = None, timeout: float | None = 30.0) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        return {
            "argv": argv,
            "returncode": 127,
            "stdout": "",
            "stderr": str(e),
            "raw": "",
            "normalized": "",
            "empty": True,
            "error": "not-found",
        }
    except subprocess.TimeoutExpired:
        return {
            "argv": argv,
            "returncode": 124,
            "stdout": "",
            "stderr": "timeout",
            "raw": "",
            "normalized": "",
            "empty": True,
            "error": "timeout",
        }
    raw = ((proc.stdout or "") + (proc.stderr or "")).strip()
    normalized = normalize_error(raw)
    return {
        "argv": argv,
        "returncode": int(proc.returncode),
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "raw": raw,
        "normalized": normalized,
        "empty": not raw or not (normalized or "").strip(),
        "error": "",
    }


def compare(cmd_a: list[str], cmd_b: list[str], *, cwd: str | None = None, timeout: float | None = 30.0) -> dict[str, Any]:
    """Compare normalized outputs of two commands.

    Exit guidance: 0 when they differ, 1 when identical (stale_identical),
    2 when either result is empty (reported separately, not conflated with identical).
    """
    a = _run(cmd_a, cwd=cwd, timeout=timeout)
    b = _run(cmd_b, cwd=cwd, timeout=timeout)
    out: dict[str, Any] = {"a": a, "b": b, "stale_identical": False}
    if a["empty"] or b["empty"]:
        if a["empty"] and b["empty"]:
            kind = "empty-both"
        elif a["empty"]:
            kind = "empty-a"
        else:
            kind = "empty-b"
        out.update({"result": EMPTY, "empty": kind, "exit": 2})
        return out
    if a["normalized"] == b["normalized"]:
        out.update({"result": IDENTICAL, "stale_identical": True, "exit": 1})
        return out
    out.update({"result": DIFFERENT, "exit": 0})
    return out
