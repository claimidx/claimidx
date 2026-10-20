"""Hooks and tests never write to the checkout that ran them.

Git exports GIT_DIR / GIT_WORK_TREE / GIT_INDEX_FILE to hooks. On 2026-10-20 a pre-push run on Windows let
pytest's temp-repo `git init` / `git commit` inherit them: the real checkout got core.bare=true and test
commits on its branch. The gate, the smoke, the hooks, and conftest now strip those variables.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _gate():
    spec = importlib.util.spec_from_file_location("gate_iso", ROOT / "scripts" / "gate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _real_repo(tmp_path: Path) -> Path:
    real = tmp_path / "real"
    real.mkdir()
    _git(real, "init", "-q")
    (real / "README").write_text("real\n", encoding="utf-8")
    _git(real, "add", "README")
    _git(real, "-c", "user.name=r", "-c", "user.email=r@r", "commit", "-qm", "real")
    return real


def _snapshot(real: Path) -> tuple[str, str, str, str]:
    cfg = (real / ".git" / "config").read_text(encoding="utf-8")
    return cfg, _git(real, "rev-parse", "HEAD"), _git(real, "rev-list", "--count", "--all"), _git(real, "config", "--bool", "core.bare")


def _hook_env(real: Path) -> dict[str, str]:
    """What git hands a hook: the real checkout's GIT_DIR, work tree and index."""
    env = dict(os.environ)
    env.update(
        {
            "GIT_DIR": str(real / ".git"),
            "GIT_WORK_TREE": str(real),
            "GIT_INDEX_FILE": str(real / ".git" / "index"),
            "GIT_PREFIX": "",
        }
    )
    return env


def test_isolated_env_strips_repo_locating_git_vars():
    gate = _gate()
    hostile = {
        "PATH": "/bin",
        "GIT_DIR": "/x/.git",
        "GIT_WORK_TREE": "/x",
        "GIT_INDEX_FILE": "/x/.git/index",
        "GIT_COMMON_DIR": "/x/.git",
        "GIT_CONFIG_PARAMETERS": "'core.bare'='true'",
        "GIT_CONFIG_KEY_0": "core.bare",
        "GIT_CONFIG_VALUE_0": "true",
        "GIT_EXEC_PATH": "/usr/lib/git-core",
    }
    clean = gate.isolated_env(hostile)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        assert key not in clean
    assert clean["PATH"] == "/bin" and clean["GIT_EXEC_PATH"] == "/usr/lib/git-core"


def test_gate_stage_subprocess_does_not_see_hook_git_env(tmp_path: Path, monkeypatch):
    gate = _gate()
    real = _real_repo(tmp_path)
    for k, v in _hook_env(real).items():
        monkeypatch.setenv(k, v)
    probe = "import os, sys; sys.exit(1 if any(os.environ.get(k) for k in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE')) else 0)"
    gate._check("probe", [sys.executable, "-c", probe])  # raises GateError if the child inherited them


def test_pytest_under_hook_env_leaves_the_real_repo_untouched(tmp_path: Path):
    """Run git-heavy tests (temp `git init`, `git commit`, `git clone`) with a hook's env aimed at a real repo."""
    real = _real_repo(tmp_path)
    before = _snapshot(real)
    assert before[3] == "false"
    tests = ["tests/test_cleanroom.py", "tests/test_apply.py::test_apply_plan_then_yes_for_a_patch"]
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
        cwd=ROOT,
        env=_hook_env(real),
        capture_output=True,
        text=True,
        timeout=600,
    )
    after = _snapshot(real)
    assert after == before, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert after[3] == "false"  # core.bare stays false
    assert _git(real, "log", "--format=%s") == "real"  # no commits added
    assert proc.returncode == 0, proc.stdout[-2000:]


@pytest.mark.skipif(sys.platform == "win32" or shutil.which("sh") is None, reason="needs a POSIX sh")
def test_pre_push_hook_isolation_unsets_git_env(tmp_path: Path):
    real = _real_repo(tmp_path)
    script = ". ./.githooks/isolate-git-env.sh; env"
    out = subprocess.run(["sh", "-c", script], cwd=ROOT, env=_hook_env(real), capture_output=True, text=True, check=True).stdout
    names = {line.split("=", 1)[0] for line in out.splitlines() if "=" in line}
    assert not names & {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX"}
    hook = (ROOT / ".githooks" / "pre-push").read_text(encoding="utf-8")
    assert hook.index(". ./.githooks/isolate-git-env.sh") < hook.index("exec ")
