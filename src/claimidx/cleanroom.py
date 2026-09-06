"""Clean-room replay: prove that fix.b produces the hold, not merely that this tree holds.

`claim --yes` replays eval.cmd in the working tree, where the agent already
applied the fix by hand. That proves the tree's state; it does not prove
the remedy written into fix.b. A pin on the wrong name (`yaml` for PyYAML)
passes that replay and fails the next agent's `apply`.

The clean room closes that gap: clone the tree at HEAD (the state before an
uncommitted fix), check that the eval misses there, apply fix.b with the
same code path `claimidx apply` uses, then replay through the graduation
gate. Only a hold produced that way mints `nr`. When the room cannot run
(not a git tree, a prose remedy) the caller falls back to the working-tree
replay and says so; when the room runs and fails, nothing is minted and the
reason is loud.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from .models import Claim


def git_top(cwd: str) -> str | None:
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    top = (proc.stdout or "").strip()
    return top if proc.returncode == 0 and top else None


def _subdir(cwd: str, top: str) -> str:
    """cwd relative to the repo top, on real paths: git prints the long form while a caller may hold an 8.3 alias
    or a different case, and a relpath between the two escapes the clone."""
    a = os.path.normcase(os.path.realpath(cwd))
    b = os.path.normcase(os.path.realpath(top))
    rel = os.path.relpath(a, b)
    if rel == "." or rel.startswith(".."):
        return ""
    return rel


def _venv_for(room: str) -> str | None:
    """A Python pin needs an interpreter in the room; the working tree's venv is not the room's."""
    venv = os.path.join(room, ".venv")
    try:
        proc = subprocess.run([sys.executable, "-m", "venv", venv], capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return f"venv failed: {e}"
    return None if proc.returncode == 0 else f"venv failed: {(proc.stderr or proc.stdout)[-200:]}"


def clean_room(claim: Claim, cwd: str, *, db, own: str | None, trust_eval: bool = False) -> dict[str, Any]:
    """Run the room for `claim` against the tree at `cwd`. See module docstring for what the keys mean."""
    out: dict[str, Any] = {"ran": False, "recorded": False}
    if claim.fix.k not in {"pin", "patch", "constraint"}:
        out["reason"] = f"{claim.fix.k} remedy is applied by hand; nothing mechanical to prove"
        return out
    if claim.fix.k == "patch" and not (claim.fix.b or "").lstrip().startswith("diff --git"):
        out["reason"] = "fix.b is a description, not a diff; nothing mechanical to prove"
        return out
    top = git_top(cwd)
    if not top:
        out["reason"] = "not a git tree; a clean clone needs a commit to start from"
        return out
    tmp = tempfile.mkdtemp(prefix="cix-room-")
    clone = os.path.join(tmp, "tree")
    try:
        try:
            proc = subprocess.run(["git", "clone", "-q", "--no-hardlinks", top, clone], capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            out["reason"] = f"clone failed: {e}"
            return out
        if proc.returncode != 0:
            out["reason"] = "clone failed: " + (proc.stderr or "")[-200:].strip()
            return out
        room = os.path.join(clone, _subdir(cwd, top)) if _subdir(cwd, top) else clone
        out["ran"] = True
        if (claim.eco or "").lower() == "py" and claim.fix.k in {"pin", "constraint"}:
            err = _venv_for(room)
            if err:
                out["reason"] = err
                return out
        from .evaltrust import eval_trust
        from .sandbox import replay
        from .store import DEFAULT_DB, Store

        store = Store(db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB))
        trust = eval_trust(store, claim, override=trust_eval)
        before = replay(claim.eval.cmd, claim.eval.expect, cwd=room, trust=trust)
        out["before"] = before.as_dict()
        if before.held:
            out["reason"] = "eval already holds in a clean clone of HEAD before fix.b: not discriminating (is the fix already committed?)"
            return out
        if not before.ran:
            out["reason"] = f"eval could not run in the clean clone: {before.reason}"
            return out
        from .apply import plan, run_plan

        p = plan(claim, room)
        if p.get("manual"):
            out["reason"] = "fix.b is not mechanical in a clean clone: " + str(p["manual"])[:200]
            return out
        res = run_plan(p)
        out["applied"] = bool(res.get("ok"))
        if not res.get("ok"):
            last = (res.get("steps") or [{}])[-1]
            why = (last.get("error") or last.get("stderr") or "").strip()[-300:]
            out["reason"] = "fix.b does not apply in a clean clone: " + (why or f"rc {last.get('rc')}")
            return out
        from .claim import _replay_now

        rep = _replay_now(claim.id, db=db, own=own, cwd=room, trust_eval=trust_eval, detail_extra={"clean_room": True}, mode="clean-room")
        out["after"] = rep
        out["after_held"] = bool(rep.get("held"))
        out["recorded"] = bool(rep.get("recorded"))
        if not rep.get("held"):
            out["reason"] = "fix.b applied in a clean clone, but the eval did not hold: " + str(rep.get("reason") or "")
        elif not rep.get("recorded"):
            out["reason"] = "held in a clean clone, not recorded: " + str(rep.get("reason") or "")
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
