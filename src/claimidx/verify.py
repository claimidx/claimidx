"""Replay evals. Confirm when they hold, fail only on a proven miss, skip otherwise.

Does not apply patch/config trees. For `fix.k=pin` + `python -c` evals, may install
the pin into a throwaway venv and rerun. Builtin `true`/`false` cannot discriminate
and are skipped. Missing trees, missing interpreters, and evals that cannot prove
the pin are skips, not fails. `--harness` is two-state: confirm only if unpinned
misses and the pin holds.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .models import Claim
from .policy import eval_allowed, split_eval, _norm_head
from .sandbox import replay
from .store import Store
from .team import resolve_owner

_PIN = re.compile(r"^[A-Za-z0-9_.-]+(?:[<>=!~]=?[^ \n]+)?$")
_MISSING = re.compile(
    r"modulenotfounderror|no module named|cannot find module|not found|"
    r"errno 2|the system cannot find|is not recognized|command not found|"
    r"no such file|goproxy|cannot find package|"
    r"neither setup\.py nor pyproject\.toml|file 'setup\.py'|pyproject\.toml|"
    r"no tests ran|no makefile|makefile:|composer|could not find gem|gem: |"
    r"bundler|mvn:|gradle",
    re.I,
)
_SKIP_HEADS_WITHOUT_TREE = {
    "npx", "npm", "go", "cargo", "rustc", "docker", "pytest",
    "mvn", "gradle", "composer", "bundle", "bundler", "gem", "php", "make",
}
_TAUTOLOGY = re.compile(
    r"^(python3?|node|go|cargo|rustc|npm|npx|docker|uv|php|ruby|java)(?:\.exe)?\s+(--version|-v|-V|version)\s*$",
    re.I,
)
_WRAPPER = re.compile(
    r"node\s+-e.*spawnSync\(\s*['\"](cargo|rustc|go|docker|npx|npm|composer|gem|bundle|bundler|mvn|gradle|make|php|ruby|pip)['\"]",
    re.I | re.S,
)


def _head(cmd: str) -> str:
    ok, _ = eval_allowed(cmd)
    if not ok:
        return ""
    try:
        _, parts = split_eval(cmd)
    except ValueError:
        return ""
    return _norm_head(parts[0]) if parts else ""


def _pin_specs(fix_b: str) -> list[str]:
    line = (fix_b or "").strip().splitlines()[0] if fix_b else ""
    if not line:
        return []
    line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
    line = re.sub(
        r"^(?:pip3?|uv|python3?\s+-m\s+pip)\s+install\s+",
        "",
        line,
        count=1,
        flags=re.I,
    ).strip()
    rest = line
    line = re.split(r"\s{2,}|\s+\(", line, maxsplit=1)[0].strip()
    out: list[str] = []
    for part in re.split(r"\s+and\s+|,\s*", line):
        token = part.strip().strip("\"'")
        if _PIN.fullmatch(token) and not token.lower().startswith("pip"):
            out.append(token)
    if not out:
        for m in re.finditer(r"[A-Za-z][A-Za-z0-9_.-]*[<>=!~]=?[^ \n,#]+", rest):
            token = m.group(0).strip().strip("\"'")
            if _PIN.fullmatch(token) and token not in out:
                out.append(token)
    return out


def _pin_spec(fix_b: str) -> str | None:
    specs = _pin_specs(fix_b)
    return specs[0] if specs else None


def _pkg_name(spec: str) -> str:
    return re.split(r"[<>=!~\[]", spec, maxsplit=1)[0].strip()


def _dist_key(name: str) -> str:
    return (name or "").lower().replace("_", "-")


def _dep_pip(dep: list[str] | None) -> list[str]:
    """Map claim.dep entries like name@1.2.3 to pip requirement strings."""
    out: list[str] = []
    for raw in dep or []:
        d = (raw or "").strip()
        if not d:
            continue
        if "@" in d:
            name, _, ver = d.partition("@")
            name, ver = name.strip(), ver.strip()
            if (not name) or "/" in name or not _PIN.fullmatch(name):
                continue
            if ver and re.fullmatch(r"[A-Za-z0-9_.+-]+", ver):
                out.append(f"{name}=={ver}")
        elif _PIN.fullmatch(d) and not d.lower().startswith("pip"):
            out.append(d)
    return out


def _install_plan(specs: list[str], dep: list[str] | None) -> tuple[list[str], list[str]]:
    """Broken combo (deps, else unpinned names) then pin overlay."""
    names = [_pkg_name(s) for s in specs if _pkg_name(s)]
    broken: dict[str, str] = {}
    for spec in _dep_pip(dep):
        pkg = _pkg_name(spec)
        if pkg:
            broken[_dist_key(pkg)] = spec
    for name in names:
        k = _dist_key(name)
        if k not in broken:
            broken[k] = name
    fixed = dict(broken)
    for spec in specs:
        pkg = _pkg_name(spec)
        if pkg:
            fixed[_dist_key(pkg)] = spec
    return list(broken.values()), list(fixed.values())


def _eval_targets_pin(cmd: str, names: list[str]) -> bool:
    """True when eval mentions a pinned dist (or dist with '-' → '_')."""
    blob = (cmd or "").lower()
    if not blob:
        return False
    for name in names:
        n = (name or "").lower().strip()
        if not n:
            continue
        ident = n.replace("-", "_")
        for token in dict.fromkeys((n, ident)):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])", blob):
                return True
    return False


def _replay_py(py: Path, cmd: str, expect: int):
    old = os.environ.get("CLAIMIDX_PYTHON")
    os.environ["CLAIMIDX_PYTHON"] = str(py)
    try:
        return replay(cmd, expect)
    finally:
        if old is None:
            os.environ.pop("CLAIMIDX_PYTHON", None)
        else:
            os.environ["CLAIMIDX_PYTHON"] = old


def harness(c: Claim, scratch: Path) -> dict:
    """Two-state pin replay: unpinned then pin.

    Confirm only if the eval discriminates (unpinned misses, pin holds).
    Skip when the setup or eval cannot prove the pin. Fail only on a
    proven miss (pin applied, eval still misses a pin-targeted command,
    or the pin regresses an eval that already held).
    """
    specs = _pin_specs(c.fix.b) if c.fix.k == "pin" else []
    if not specs or _head(c.eval.cmd) not in _RUNNABLE_HEADS:
        return {"action": "skip", "reason": "harness-no-repro", "id": c.id}
    names = [_pkg_name(s) for s in specs if _pkg_name(s)]
    if not names:
        return {"action": "skip", "reason": "harness-no-repro", "id": c.id}
    broken_req, fixed_req = _install_plan(specs, c.dep)
    target_names = names + [_pkg_name(s) for s in _dep_pip(c.dep)]
    venv = scratch / "venv"
    try:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            check=True,
            capture_output=True,
            timeout=60,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"action": "skip", "reason": f"harness-venv:{e}", "id": c.id}
    py = _venv_python(venv)
    pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q"]
    try:
        br = subprocess.run(pip + broken_req, capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError) as e:
        return {"action": "skip", "reason": f"harness-broken-install:{e}", "id": c.id}
    if br.returncode != 0:
        return {
            "action": "skip",
            "reason": "harness-broken-install",
            "id": c.id,
            "stderr": (br.stderr or "")[-300:],
        }
    broken = _replay_py(py, c.eval.cmd, c.eval.expect)
    try:
        fx = subprocess.run(pip + fixed_req, capture_output=True, text=True, timeout=180)
    except (subprocess.SubprocessError, OSError) as e:
        return {"action": "skip", "reason": f"harness-pin-install:{e}", "id": c.id}
    if fx.returncode != 0:
        return {
            "action": "skip",
            "reason": "harness-pin-install",
            "id": c.id,
            "stderr": (fx.stderr or "")[-300:],
        }
    fixed = _replay_py(py, c.eval.cmd, c.eval.expect)
    applied = " ".join(specs)
    out = {
        "id": c.id,
        "broken": broken.as_dict(),
        "fixed": fixed.as_dict(),
        "applied": applied,
        "broken_req": broken_req,
        "fixed_req": fixed_req,
    }
    if (not broken.held) and fixed.held:
        return {**out, "action": "confirm", "reason": "harness-discriminates"}
    if broken.held and fixed.held:
        return {**out, "action": "skip", "reason": "harness-no-discriminate"}
    if broken.held and not fixed.held:
        return {**out, "action": "fail", "reason": "harness-eval-miss"}
    if not _eval_targets_pin(c.eval.cmd, target_names):
        return {**out, "action": "skip", "reason": "harness-no-repro"}
    return {**out, "action": "fail", "reason": "harness-eval-miss"}


def _seen_path() -> Path:
    return Path(os.environ.get("CLAIMIDX_VERIFY_SEEN") or (Path.home() / ".claimidx" / "verify-seen.json"))


def load_seen() -> dict:
    p = _seen_path()
    if not p.exists():
        return {"day": "", "ids": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"day": "", "ids": []}


def save_seen(st: dict) -> None:
    p = _seen_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st, indent=2) + "\n", encoding="utf-8")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_RUNNABLE_HEADS = {"python", "python3"}
_PIP_EDITABLE = re.compile(r"\bpip\b.+\binstall\b.*(\s-e\s|\s\.(?:\s|$))", re.I)


def is_runnable(c: Claim) -> bool:
    """Self-contained evals we can actually execute in an empty scratch dir."""
    cmd = (c.eval.cmd or "").strip()
    if not cmd or _TAUTOLOGY.match(cmd) or _WRAPPER.search(cmd):
        return False
    head = _head(cmd)
    if head not in _RUNNABLE_HEADS:
        return False
    if re.search(r"\bpytest\b", cmd) or _PIP_EDITABLE.search(cmd):
        return False
    return True


def is_harnessable(c: Claim) -> bool:
    """Pins whose eval we can two-state replay in an empty scratch venv."""
    if c.fix.k != "pin":
        return False
    if not is_runnable(c):
        return False
    return bool(_pin_specs(c.fix.b))


def pick(
    claims: list[Claim],
    *,
    k: int,
    ids: list[str] | None,
    seen: set[str],
    runnable: bool = False,
    harness_mode: bool = False,
) -> list[Claim]:
    if ids:
        by_id = {c.id: c for c in claims}
        return [by_id[i] for i in ids if i in by_id][:k]
    wanted = []
    for c in claims:
        if c.id in seen or c.st == "rejected":
            continue
        if harness_mode:
            if not is_harnessable(c):
                continue
        elif runnable:
            if not is_runnable(c):
                continue
        else:
            head = _head(c.eval.cmd)
            if head in {"true", "false"}:
                continue
            if _TAUTOLOGY.match((c.eval.cmd or "").strip()):
                continue
            if _WRAPPER.search(c.eval.cmd or ""):
                continue
            if not head:
                continue
        wanted.append(c)

    def key(c: Claim):
        pri = {"contested": 0, "proposed": 1, "stale": 2, "confirmed": 3}.get(c.st, 4)
        return (pri, -int(c.nf or 0), int(c.nc or 0), c.id)

    wanted.sort(key=key)
    return wanted[:k]


def _venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _apply_pin_and_replay(c: Claim, tmp: Path) -> dict | None:
    spec = _pin_spec(c.fix.b)
    if c.fix.k != "pin" or not spec:
        return None
    if _head(c.eval.cmd) not in {"python", "python3"}:
        return None
    venv = tmp / "venv"
    try:
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True, capture_output=True, timeout=60)
    except (subprocess.SubprocessError, OSError):
        return None
    py = _venv_python(venv)
    pip = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", spec]
    try:
        inst = subprocess.run(pip, capture_output=True, text=True, timeout=120)
    except (subprocess.SubprocessError, OSError) as e:
        return {"action": "skip", "reason": f"pin-install-error:{e}", "id": c.id}
    if inst.returncode != 0:
        return {"action": "skip", "reason": "pin-install-failed", "id": c.id, "stderr": (inst.stderr or "")[-300:]}
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv)
    bindir = str(py.parent)
    env["PATH"] = bindir + os.pathsep + env.get("PATH", "")
    result = replay(c.eval.cmd, c.eval.expect, cwd=str(tmp))
    # replay uses sys.executable for python; force venv by rewriting via env PATH python.exe first
    # On Windows resolve_argv uses sys.executable, ignoring venv. Run eval argv with venv python instead.
    ok, reason = eval_allowed(c.eval.cmd)
    if not ok:
        return {"action": "skip", "reason": reason, "id": c.id}
    try:
        extra_env, parts = split_eval(c.eval.cmd)
    except ValueError as e:
        return {"action": "skip", "reason": str(e), "id": c.id}
    if _norm_head(parts[0]) in {"python", "python3"}:
        argv = [str(py), *parts[1:]]
        env.update(extra_env)
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=45, cwd=str(tmp), env=env, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"action": "skip", "reason": "timeout", "id": c.id}
        held = proc.returncode == c.eval.expect
        if held:
            return {
                "action": "confirm",
                "reason": "held-pin",
                "id": c.id,
                "rc": proc.returncode,
                "stderr": (proc.stderr or "")[-300:],
                "applied": spec,
            }
        if not _eval_targets_pin(c.eval.cmd, [_pkg_name(spec)]):
            return {
                "action": "skip",
                "reason": "pin-eval-unproven",
                "id": c.id,
                "rc": proc.returncode,
                "stderr": (proc.stderr or "")[-300:],
                "applied": spec,
            }
        return {
            "action": "fail",
            "reason": "eval-miss-pin",
            "id": c.id,
            "rc": proc.returncode,
            "stderr": (proc.stderr or "")[-300:],
            "applied": spec,
        }
    return {"action": "skip", "reason": result.reason, "id": c.id, "replay": result.as_dict()}


def decide(c: Claim, *, scratch: Path) -> dict:
    cmd = c.eval.cmd
    ok, reason = eval_allowed(cmd)
    if not ok:
        return {"action": "skip", "reason": reason, "id": c.id}
    head = _head(cmd)
    if head in {"true", "false"}:
        return {"action": "skip", "reason": "builtin-eval", "id": c.id}
    if _TAUTOLOGY.match((cmd or "").strip()):
        return {"action": "skip", "reason": "tautology-eval", "id": c.id}
    wrap = _WRAPPER.search(cmd or "")
    if wrap:
        inner = wrap.group(1).lower()
        result = replay(cmd, c.eval.expect, cwd=str(scratch))
        # node -e spawnSync(cargo) returns status null → process.exit(null) → rc 0 when cargo is missing
        blob = (result.stderr or "") + " " + (result.stdout or "")
        if "enoent" in blob.lower() or inner in _SKIP_HEADS_WITHOUT_TREE:
            return {
                "action": "skip",
                "reason": f"wrapper-eval:{inner}",
                "id": c.id,
                "replay": result.as_dict(),
            }
    if head in _SKIP_HEADS_WITHOUT_TREE:
        result = replay(cmd, c.eval.expect, cwd=str(scratch))
        if (result.reason or "").startswith("eval-precondition"):
            return {"action": "skip", "reason": result.reason, "id": c.id, "replay": result.as_dict()}
    pin = _apply_pin_and_replay(c, scratch)
    if pin:
        return pin
    result = replay(cmd, c.eval.expect, cwd=str(scratch))
    info = result.as_dict()
    if not result.ran:
        return {"action": "skip", "reason": result.reason, "id": c.id, "replay": info}
    if result.held:
        return {"action": "confirm", "reason": "held", "id": c.id, "replay": info}
    blob = (result.stderr or "") + " " + (result.stdout or "")
    if is_runnable(c):
        return {"action": "fail", "reason": "eval-miss", "id": c.id, "replay": info}
    if _MISSING.search(blob):
        return {"action": "skip", "reason": "missing-dep-or-tool", "id": c.id, "replay": info}
    return {"action": "fail", "reason": "eval-miss", "id": c.id, "replay": info}


def _project_ledger(path: Path, updates: list[Claim]) -> int:
    if not path.is_file() or not updates:
        return 0
    by_id = {c.id: c for c in updates}
    lines = path.read_text(encoding="utf-8").splitlines()
    n = 0
    out = []
    for line in lines:
        if not line.strip():
            continue
        row = json.loads(line)
        cid = row.get("id")
        if cid in by_id:
            c = by_id[cid]
            row["nc"] = c.nc
            row["nf"] = c.nf
            row["st"] = c.st
            n += 1
            out.append(json.dumps(row, ensure_ascii=False))
        else:
            out.append(line)
    if n:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def run(
    store: Store,
    *,
    k: int = 8,
    ids: list[str] | None = None,
    own: str | None = None,
    dry_run: bool = False,
    ledger: str | Path | None = None,
    runnable: bool = False,
    harness_mode: bool = False,
) -> dict:
    actor = resolve_owner(own)
    seen_st = load_seen()
    day = _today()
    if seen_st.get("day") != day:
        seen_st = {"day": day, "ids": []}
    seen = set(seen_st.get("ids") or [])
    chosen = pick(
        store.all(),
        k=k,
        ids=ids,
        seen=seen,
        runnable=runnable,
        harness_mode=harness_mode,
    )
    results = []
    changed: list[Claim] = []
    scratch_root = Path(tempfile.mkdtemp(prefix="cix-verify-"))
    try:
        for c in chosen:
            work = scratch_root / c.id
            work.mkdir()
            decision = harness(c, work) if harness_mode else decide(c, scratch=work)
            action = decision["action"]
            if not dry_run:
                if action == "confirm":
                    store.confirm(c.id, actor, replayed=True)
                    changed.append(store.get(c.id))
                    seen.add(c.id)
                elif action == "fail":
                    store.fail(c.id, actor, note=decision.get("reason") or "verify eval-miss")
                    changed.append(store.get(c.id))
                    seen.add(c.id)
                elif action == "skip":
                    reason = decision.get("reason") or ""
                    if reason in {
                        "harness-no-repro",
                        "harness-no-discriminate",
                        "harness-broken-install",
                        "harness-pin-install",
                        "pin-eval-unproven",
                    }:
                        seen.add(c.id)
            decision["st"] = (store.get(c.id).st if store.get(c.id) else c.st)
            results.append(decision)
        if not dry_run:
            seen_st["ids"] = sorted(seen)
            save_seen(seen_st)
            if ledger:
                _project_ledger(Path(ledger), [c for c in changed if c])
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)
    counts = {"confirm": 0, "fail": 0, "skip": 0}
    for r in results:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    return {
        "n": len(results),
        "dry_run": dry_run,
        "counts": counts,
        "results": results,
    }
