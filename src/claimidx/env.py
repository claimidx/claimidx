"""What the agent should never have to type: ecosystem, runtime, and the last failure.

`infer_env(cwd)` reads tree markers and the executing interpreters so `eco`
and `rt` default correctly. `remember_failure` / `last_failure` let the hook
leave the error behind for `claimidx claim`, so the claim can be drafted
without re-pasting anything. Both are best-effort and fail open.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
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
    "npm": ("npm err", "cannot find module '", "node_modules", "eresolve", "typeerror: cannot read propert"),  # node quotes the module
    "py": ("traceback (most recent call last)", "modulenotfounderror", "importerror", "pip "),
    "go": ("go: ", "go.mod", "undefined:", "no required module provides package", "cannot find package"),
    "rust": ("cargo", "error[e", "unresolved import", "can't find crate", "undeclared crate", "cannot find module or crate", "cannot find crate"),
    "java": ("error: package ", "could not find artifact", "failed to execute goal", "could not resolve dependencies", "required by:", "gradle", "maven"),
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
    elif eco == "go":
        rt = _go_rt()
    elif eco == "rust":
        rt = _rust_rt()
    elif eco == "java":
        rt = _java_rt()
    return {"eco": eco, "rt": rt, "cwd": str(root)}


def _tool_version(argv: list[str], pattern: str, label: str) -> str:
    """`label@X.Y` from a toolchain's version banner (stdout or stderr), "" when absent."""
    if not shutil.which(argv[0]):
        return ""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(pattern, (proc.stdout or "") + "\n" + (proc.stderr or ""))
    return f"{label}@{m.group(1)}" if m else ""


def _go_rt() -> str:
    return _tool_version(["go", "version"], r"go(\d+\.\d+)", "go")


def _rust_rt() -> str:
    return _tool_version(["rustc", "--version"], r"rustc (\d+\.\d+)", "rust")


def _java_rt() -> str:
    return _tool_version(["java", "-version"], r"version \"(\d+)", "java")


_SITE_FRAME = re.compile(r"""[\\/]site-packages[\\/]([A-Za-z0-9_]+)[\\/]""")
_NODE_FRAME = re.compile(r"""[\\/]node_modules[\\/](@[A-Za-z0-9_.-]+[\\/][A-Za-z0-9_.-]+|[A-Za-z0-9_.-]+)[\\/]""")
_RUNNER_PKGS = {"_pytest", "pytest", "pluggy", "unittest", "nose", "coverage", "pip", "setuptools", "importlib_metadata", "typing_extensions"}


def deps_from_traceback(text: str, cwd: str | os.PathLike[str] | None = None, eco: str = "") -> list[str]:
    """The package the deepest traceback frame lives in, as an installed `name@ver` pin.

    Only the raising package: an error surfacing through pytest and pluggy
    frames is not about pytest. Empty when no third-party frame raised.
    """
    if not text:
        return []
    root = Path(cwd or os.getcwd())
    if eco in {"npm", "node"}:
        names = [m.group(1) for m in _NODE_FRAME.finditer(text)]
        for name in reversed(names):
            pkg = root / "node_modules" / name / "package.json"
            try:
                ver = json.loads(pkg.read_text(encoding="utf-8")).get("version")
            except (OSError, ValueError):
                continue
            if ver:
                return [f"{name}@{ver}"]
        return []
    names = [m.group(1) for m in _SITE_FRAME.finditer(text) if m.group(1) not in _RUNNER_PKGS]
    if not names:
        return []
    deepest = names[-1]
    from .sandbox import project_python

    py = project_python(root) or sys.executable
    code = (
        "import json,sys\nfrom importlib.metadata import packages_distributions, version\n"
        "n=sys.argv[1]; out=''\n"
        "for d in (packages_distributions().get(n) or []):\n"
        "    try: out=d+'@'+version(d); break\n"
        "    except Exception: pass\n"
        "print(out)"
    )
    try:
        import subprocess

        proc = subprocess.run([py, "-c", code, deepest], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    pin = (proc.stdout or "").strip()
    return [pin] if proc.returncode == 0 and pin else []


def tree_eval(cwd: str | os.PathLike[str] | None, eco: str = "") -> str:
    """The tree's own check, when it has one: npm test, pytest, tsc, go build, cargo check. "" otherwise."""
    root = Path(cwd or os.getcwd())
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, ValueError):
            scripts = {}
        if (root / "tsconfig.json").exists() and (root / "node_modules" / ".bin" / "tsc").exists():
            return "npx tsc --noEmit"
        if "test" in scripts and "no test specified" not in str(scripts.get("test")):
            return "npm test"
        if "build" in scripts:
            return "npm run build"
    if eco in ("", "py"):
        if (
            any((root / m).exists() for m in ("pytest.ini", "conftest.py", "tests", "test"))
            or (root / "pyproject.toml").exists()
            and "pytest" in (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        ):
            return "python -m pytest -q"  # the interpreter puts cwd on sys.path; a bare `pytest` in an uninstalled tree does not
    if (root / "go.mod").exists():
        return "go build ./..."
    if (root / "Cargo.toml").exists():
        return "cargo check"
    if (root / "pom.xml").exists():
        return "mvn -q compile"
    if (root / "build.gradle").exists() or (root / "build.gradle.kts").exists():
        return "gradle -q compileJava"
    return ""


def installed_version(name: str, eco: str = "", cwd: str | os.PathLike[str] | None = None) -> str:
    """`name@ver` for an installed dependency, or "" when unknown.

    Python versions are read from the tree's own venv when it has one — that
    is the interpreter the claim is about — and only then from this process.
    """
    if not name:
        return ""
    root = Path(cwd or os.getcwd())
    if eco in {"npm", "node"} or name.startswith("@"):
        pkg = root / "node_modules" / name / "package.json"
        try:
            ver = json.loads(pkg.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError):
            return ""
        return f"{name}@{ver}" if ver else ""
    if eco == "go":
        return _go_module_version(name, root)
    if eco == "rust":
        return _cargo_lock_version(name, root)
    if eco == "java":
        return _java_coordinate_version(name, root)
    from .sandbox import project_python

    own = project_python(root)
    if own:
        try:
            proc = subprocess.run([own, "-c", _DIST_VERSION_CODE, name], capture_output=True, text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        if proc is not None and proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return _dist_version(name)


def _go_module_version(pkg: str, root: Path) -> str:
    """`module@version` providing a Go package path, via the tree's `go list`. "" when unknown or std."""
    pkg = pkg.split("@", 1)[0]
    if not shutil.which("go") or not re.match(r"^[A-Za-z0-9_][A-Za-z0-9_./~-]*$", pkg):
        return ""
    try:
        proc = subprocess.run(
            ["go", "list", "-f", "{{if .Module}}{{.Module.Path}}@{{.Module.Version}}{{end}}", pkg],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    out = (proc.stdout or "").strip()
    return out if proc.returncode == 0 and "@" in out and not out.endswith("@") else ""


def _cargo_lock_version(crate: str, root: Path) -> str:
    """`crate@version` from Cargo.lock. "" when the lockfile lacks it."""
    import tomllib

    crate = crate.split("@", 1)[0]
    try:
        data = tomllib.loads((root / "Cargo.lock").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    for pkg in data.get("package") or []:
        if isinstance(pkg, dict) and pkg.get("name") == crate and pkg.get("version"):
            return f"{crate}@{pkg['version']}"
    return ""


_POM_DEP = re.compile(r"<dependency>(.*?)</dependency>", re.S)


def _java_coordinate_version(coord: str, root: Path) -> str:
    """`group:artifact@version` as pinned in pom.xml or build.gradle(.kts). "" when absent or a property."""
    coord = coord.split("@", 1)[0]
    if coord.count(":") != 1:
        return ""
    group, artifact = coord.split(":")
    pom = root / "pom.xml"
    if pom.is_file():
        try:
            text = pom.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for block in _POM_DEP.findall(text):
            g = re.search(r"<groupId>\s*([^<\s]+)\s*</groupId>", block)
            a = re.search(r"<artifactId>\s*([^<\s]+)\s*</artifactId>", block)
            v = re.search(r"<version>\s*([^<\s]+)\s*</version>", block)
            if g and a and g.group(1) == group and a.group(1) == artifact and v and not v.group(1).startswith("$"):
                return f"{coord}@{v.group(1)}"
    for name in ("build.gradle.kts", "build.gradle"):
        f = root / name
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"[\"']" + re.escape(coord) + r":([^\"'\s:]+)[\"']", text)
        if m and not m.group(1).startswith("$"):
            return f"{coord}@{m.group(1)}"
    return ""


# `import yaml` is provided by PyYAML: a pin names what pip installs, not the module.
# Try the name as a distribution first, then map it as an import name.
_DIST_VERSION_CODE = (
    "import sys\n"
    "from importlib.metadata import packages_distributions, version\n"
    "n = sys.argv[1]\n"
    "try:\n"
    "    print(n + '@' + version(n))\n"
    "except Exception:\n"
    "    for d in packages_distributions().get(n) or []:\n"
    "        try:\n"
    "            print(d + '@' + version(d)); break\n"
    "        except Exception:\n"
    "            pass\n"
)


def _dist_version(name: str) -> str:
    """`Dist@ver` from this interpreter: `name` as a distribution, else as an import name."""
    from importlib.metadata import packages_distributions, version

    try:
        return f"{name}@{version(name)}"
    except Exception:
        pass
    try:
        dists = packages_distributions().get(name) or []
    except Exception:
        return ""
    for d in dists:
        try:
            return f"{d}@{version(d)}"
        except Exception:
            continue
    return ""


# --- last failure ---------------------------------------------------------


def last_failure_path() -> Path:
    override = os.environ.get("CLAIMIDX_LAST_FAILURE")
    if override:
        return Path(override)
    from .config import config_path

    return config_path().parent / "last_failure.json"


def remember_failure(
    err: str, *, command: str = "", cwd: str = "", eco: str = "", rt: str = "", event: str = "", fp: str = "", extra: dict[str, Any] | None = None
) -> dict[str, Any]:
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
        **(extra or {}),
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
