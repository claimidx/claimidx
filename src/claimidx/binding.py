"""Proof binding and observed dependency digests.

A zero-exit proves nothing unless it is bound to the bytes that produced it.
Two subjects, one primitive (sha256 over files or directories):

- **recipe artifacts** (excelsior X2): the files a tree-scoped eval names
  (`python check.py`, `pytest tests/x.py`) or, for bare build/test recipes,
  the tree's manifests (`package.json`, `pyproject.toml`, `go.mod`, ...).
  Bound at `publish --cwd` or at the first held replay; drift refuses `nr`.
- **dependency artifacts** (I1): the installed bytes under a `name@ver` pin
  the author observed. Recorded with `publish --observe-digest`; drift warns
  `digest_drift` (or refuses with `--strict-digest`). Never in the fingerprint.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .graph import ArtifactDigest, DepDigest, ProofBinding
from .policy import _norm_head, split_eval

_MANIFESTS = {
    "npx": ("package.json", "tsconfig.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"),
    "npm": ("package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"),
    "node": ("package.json",),
    "pytest": ("pyproject.toml", "pytest.ini", "setup.cfg", "conftest.py", "tox.ini"),
    "go": ("go.mod", "go.sum", "go.work"),
    "cargo": ("Cargo.toml", "Cargo.lock"),
    "rustc": ("Cargo.toml",),
    "mvn": ("pom.xml",),
    "mvnw": ("pom.xml",),
    "gradle": ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.lockfile"),
    "gradlew": ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.lockfile"),
    "javac": (),
    "java": (),
    "docker": ("Dockerfile", "docker-compose.yml", "compose.yml", "compose.yaml"),
    "uv": ("pyproject.toml", "uv.lock"),
    "make": ("Makefile", "makefile"),
    "test": (),
}
_REL = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./\\-]*$")
_MAX_FILES = 2000
_TEXT_MAX = 8 << 20  # files up to 8 MiB are read whole so line endings can be folded
_SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv", ".mypy_cache", ".pytest_cache", ".ruff_cache", "target", "dist", "build"}


def sha256_path(path: Path) -> str | None:
    """sha256 of a file's bytes, or of a directory's sorted (relpath, file sha256) list. None if missing.

    Text files are hashed with CRLF folded to LF: the same tree checked out under
    autocrlf must bind to the same digest, and a line ending is not a mutation.
    """
    if path.is_file():
        h = hashlib.sha256()
        try:
            small = path.stat().st_size <= _TEXT_MAX
        except OSError:
            return None
        if small:
            data = path.read_bytes()
            if b"\0" not in data[:8192]:
                data = data.replace(b"\r\n", b"\n")
            h.update(data)
            return h.hexdigest()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    if path.is_dir():
        h = hashlib.sha256()
        n = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS)
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")):
                    continue
                fp = Path(root) / name
                rel = fp.relative_to(path).as_posix()
                digest = sha256_path(fp) or ""
                h.update(rel.encode("utf-8") + b"\0" + digest.encode("ascii") + b"\n")
                n += 1
                if n >= _MAX_FILES:
                    h.update(b"...truncated\n")
                    return h.hexdigest()
        return h.hexdigest()
    return None


def _rel_tokens(parts: list[str], root: Path) -> list[str]:
    out: list[str] = []
    for tok in parts[1:]:
        if tok.startswith("-") or not _REL.match(tok) or ".." in tok.replace("\\", "/").split("/"):
            continue
        cand = root / tok
        if cand.exists() and (cand.is_file() or cand.is_dir()):
            out.append(tok.replace("\\", "/"))
    return out


def is_tree_scoped(cmd: str) -> bool:
    """A recipe whose meaning depends on files under --cwd (as opposed to a portable import/version check)."""
    try:
        _env, parts = split_eval(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    head = _norm_head(parts[0])
    if head in {"true", "false"}:
        return False
    if head in {"python", "python3", "node"}:
        args = parts[1:]
        if args and args[0] in {"-c", "-e", "--eval", "-p", "--print"}:
            return False
        if args and args[0] == "-m" and args[1:2] and args[1] not in {"pytest", "unittest", "compileall", "py_compile"}:
            return False
        return True
    if head == "cargo" and parts[1:2] == ["pkgid"]:
        return False  # observes a package in the graph, as `import x` observes an env; the manifest is what a pin changes
    if head == "go" and parts[1:2] == ["list"]:
        args = [a for a in parts[2:] if not a.startswith("-")]
        if args and not any(a.startswith(".") or a.endswith("...") for a in args):
            return False  # `go list <module/path>`: a package observation, not a tree build
    return head in _MANIFESTS


def recipe_paths(cmd: str, cwd: str | os.PathLike[str]) -> list[str]:
    """Paths under cwd this recipe is bound to: the files it names, else the tree's manifests."""
    root = Path(cwd)
    try:
        _env, parts = split_eval(cmd)
    except ValueError:
        return []
    if not parts:
        return []
    named = _rel_tokens(parts, root)
    if named:
        return named
    head = _norm_head(parts[0])
    if head in {"python", "python3"} and parts[1:2] == ["-m"]:
        head = "pytest"
    return [m for m in _MANIFESTS.get(head, ()) if (root / m).exists()]


def compute_binding(cmd: str, cwd: str | os.PathLike[str] | None, *, source: str = "ingest") -> ProofBinding | None:
    """Digest the recipe's paths under cwd. None when there is nothing to bind."""
    if not cwd or not is_tree_scoped(cmd):
        return None
    arts: list[ArtifactDigest] = []
    for rel in recipe_paths(cmd, cwd):
        hx = sha256_path(Path(cwd) / rel)
        if hx:
            arts.append(ArtifactDigest(path=rel, hex=hx))
    if not arts:
        return None
    return ProofBinding(artifacts=arts[:64], source=source)  # type: ignore[arg-type]


def binding_drift(binding: ProofBinding, cwd: str | os.PathLike[str]) -> list[str]:
    """Paths whose bytes no longer match the binding (missing counts as drift)."""
    out: list[str] = []
    for a in binding.artifacts:
        if sha256_path(Path(cwd) / a.path) != a.hex:
            out.append(a.path)
    return out


# --- dependency digests ------------------------------------------------------


def _dep_name(pin: str) -> str:
    from .public import _pkg_token

    return _pkg_token(pin)


def local_dep_digest(dep: str, cwd: str | os.PathLike[str] | None = None, eco: str = "") -> str | None:
    """Digest of the local artifact for a pin: the installed dist's RECORD, a package dir under cwd, or node_modules/<name>.

    None when nothing local corresponds to the pin, so the caller can stay silent.
    """
    name = _dep_name(dep)
    if not name:
        return None
    root = Path(cwd or os.getcwd())
    if eco in {"npm", "node"} or name.startswith("@"):
        pkg = root / "node_modules" / name
        return sha256_path(pkg) if pkg.is_dir() else None
    mod = name.replace("-", "_")
    for cand in (root / mod, root / (mod + ".py"), root / "src" / mod):
        if cand.exists():
            return sha256_path(cand)
    try:
        from importlib.metadata import distribution

        dist = distribution(name)
        for f in dist.files or []:
            if f.name == "RECORD":
                p = Path(str(dist.locate_file(f)))
                return sha256_path(p) if p.is_file() else None
    except Exception:
        return None
    return None


def observe_digests(dep: list[str], cwd: str | os.PathLike[str] | None, eco: str = "") -> list[DepDigest]:
    out: list[DepDigest] = []
    for pin in dep or []:
        hx = local_dep_digest(pin, cwd, eco)
        if hx:
            out.append(DepDigest(dep=pin, hex=hx))
    return out[:32]


def parse_digest_arg(raw: str) -> DepDigest:
    """`name@ver=sha256:<hex>` from the CLI."""
    dep, sep, rest = (raw or "").partition("=")
    alg, _, hx = rest.partition(":")
    if not sep or alg.lower() != "sha256" or not re.fullmatch(r"[0-9a-f]{64}", hx or ""):
        raise ValueError(f"observed digest must be name@ver=sha256:<64 hex>: {raw!r}")
    return DepDigest(dep=dep.strip(), hex=hx)


def digest_drift(observed: list[DepDigest], cwd: str | os.PathLike[str] | None, eco: str = "") -> list[str]:
    """Pins whose local bytes differ from the recorded digest. Pins with no local artifact are not drift."""
    out: list[str] = []
    for d in observed:
        local = local_dep_digest(d.dep, cwd, eco)
        if local and local != d.hex:
            out.append(d.dep)
    return out
