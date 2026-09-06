"""What the agent should never have to type: ecosystem, runtime, and the last failure.

`infer_env(cwd)` reads tree markers and the executing interpreters so `eco`
and `rt` default correctly. `remember_failure` / `last_failure` let the hook
leave the error behind for `claimidx claim`, so the claim can be drafted
without re-pasting anything. Both are best-effort and fail open.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

_ECO_MARKERS: list[tuple[str, tuple[str, ...]]] = [
    ("npm", ("package.json",)),
    ("py", ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "uv.lock")),
    ("go", ("go.mod", "go.work")),
    ("rust", ("Cargo.toml",)),
    ("java", ("pom.xml", "build.gradle", "build.gradle.kts")),
    ("php", ("composer.json",)),
    ("ruby", ("Gemfile",)),
]
_ECO_BY_ERR = {
    "npm": ("npm err", "cannot find module", "node_modules", "eresolve", "typeerror: cannot read propert"),
    "py": ("traceback (most recent call last)", "modulenotfounderror", "importerror", "pip "),
    "go": ("go: ", "go.mod", "undefined:"),
    "rust": ("cargo", "error[e"),
}


def _tree_eco(cwd: Path) -> str:
    found: list[str] = []
    for eco, names in _ECO_MARKERS:
        if any((cwd / n).exists() for n in names):
            found.append(eco)
    if not found:
        return ""
    # Mixed trees: prefer the ecosystem whose lockfile is present.
    if "py" in found and "npm" in found:
        return "npm" if (cwd / "package-lock.json").exists() or (cwd / "pnpm-lock.yaml").exists() else "py"
    return found[0]


def _err_eco(err: str) -> str:
    low = (err or "").lower()
    for eco, needles in _ECO_BY_ERR.items():
        if any(n in low for n in needles):
            return eco
    return ""


def _py_rt() -> str:
    v = sys.version_info
    return f"py@{v.major}.{v.minor}"


def _node_rt() -> str:
    exe = shutil.which("node")
    if not exe:
        return ""
    from .sandbox import _observe_node

    return _observe_node(exe)


def infer_env(cwd: str | os.PathLike[str] | None = None, *, err: str = "") -> dict[str, str]:
    """Best guess for eco and rt. Empty strings when nothing is knowable."""
    root = Path(cwd or os.getcwd())
    # The error text outranks the tree: an npm error in a python monorepo is still npm.
    eco = _err_eco(err) or _tree_eco(root)
    rt = ""
    if eco == "py":
        rt = _py_rt()
    elif eco == "npm":
        rt = _node_rt()
    return {"eco": eco, "rt": rt, "cwd": str(root)}


def installed_version(name: str, eco: str = "") -> str:
    """`name@ver` for an installed dependency, or "" when unknown."""
    if not name:
        return ""
    if eco in {"npm", "node"} or name.startswith("@"):
        pkg = Path(os.getcwd()) / "node_modules" / name / "package.json"
        try:
            ver = json.loads(pkg.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            return ""
        return f"{name}@{ver}" if ver else ""
    try:
        from importlib.metadata import version

        return f"{name}@{version(name)}"
    except Exception:
        return ""


# --- last failure ---------------------------------------------------------


def last_failure_path() -> Path:
    override = os.environ.get("CLAIMIDX_LAST_FAILURE")
    if override:
        return Path(override)
    from .config import config_path

    return config_path().parent / "last_failure.json"


def remember_failure(err: str, *, command: str = "", cwd: str = "", eco: str = "", rt: str = "", event: str = "", fp: str = "") -> dict[str, Any]:
    from .security import SecretError, reject_secrets, strip_control

    command = strip_control(command or "")
    try:
        reject_secrets(command)
    except SecretError:
        command = ""  # a curl -H "Authorization: Bearer …" must not land on disk
    rec = {
        "err": strip_control(err or "")[:280],
        "command": command[:400],
        "cwd": cwd or os.getcwd(),
        "eco": eco or "",
        "rt": rt or "",
        "event": event or "",
        "fp": fp or "",
        "ts": int(time.time()),
    }
    try:
        path = last_failure_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    except OSError:
        pass
    return rec


def last_failure(max_age_s: int = 6 * 3600) -> dict[str, Any] | None:
    try:
        data = json.loads(last_failure_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("err"):
        return None
    if max_age_s and int(time.time()) - int(data.get("ts") or 0) > max_age_s:
        return None
    return data


def forget_failure() -> None:
    try:
        last_failure_path().unlink()
    except OSError:
        pass
