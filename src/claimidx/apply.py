"""`claimidx apply <id>`: from a verdict to a recorded hold in one command.

The verdict says "apply fix.b, then confirm --replay". For the two remedy
kinds that are mechanical — a dependency pin and a `diff --git` patch —
this does exactly that and nothing more: install the spec with the tree's
own package manager, or `git apply` the diff, then replay the eval under
--cwd and record the result through the same gate as `confirm --replay`.

It is not an executor. `cmd`, `config`, and prose remedies are printed for
the agent to apply by hand. Without --yes it only prints the plan; a claim
that was not published here is named as such, because installing its pin
is running someone else's choice of package.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any

from .models import Claim

_PY_SPEC = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9_.+*!-]+(?:,(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9_.+*!-]+)*)?$"
)
_NPM_SPEC = re.compile(r"^(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*(?:@[A-Za-z0-9_.^~<>=|* -]+)?$")


def _pin_spec(fix_b: str) -> str:
    from .public import _pin_line

    return _pin_line(fix_b)


def plan(claim: Claim, cwd: str) -> dict[str, Any]:
    """What `apply` would run. `steps` is a list of argv; `manual` is set when the agent must act."""
    root = os.path.abspath(cwd or os.getcwd())
    kind = claim.fix.k
    body = claim.fix.b or ""
    out: dict[str, Any] = {"id": claim.id, "kind": kind, "cwd": root, "steps": [], "stdin": None}
    if kind in {"pin", "constraint"}:
        spec = _pin_spec(body)
        if claim.eco in {"npm", "node"} or spec.startswith("@"):
            if not _NPM_SPEC.match(spec):
                out["manual"] = f"not a plain npm spec; apply by hand: {body[:200]}"
                return out
            out["steps"] = [["npm", "install", spec]]
            return out
        if not _PY_SPEC.match(spec):
            out["manual"] = f"not a plain pip spec (URLs, -e, --index-url are never installed by apply); apply by hand: {body[:200]}"
            return out
        from .sandbox import project_python

        py = project_python(root)
        if not py:
            out["manual"] = f"no .venv under {root}; install {spec} into the environment your eval runs in, then confirm --replay"
            return out
        out["steps"] = [[py, "-m", "pip", "install", "--disable-pip-version-check", "-q", spec]]
        return out
    if kind == "patch":
        if not body.lstrip().startswith("diff --git"):
            out["manual"] = "fix.b is a description, not a diff; make the change by hand, then confirm --replay"
            return out
        out["steps"] = [["git", "apply", "--check", "-"], ["git", "apply", "-"]]
        out["stdin"] = body if body.endswith("\n") else body + "\n"
        return out
    if kind == "cmd":
        out["manual"] = f"cmd remedies are never executed by Claimidx; read and run it yourself if you agree with it: {body[:200]}"
        return out
    if kind == "wontfix":
        out["manual"] = "wontfix: there is nothing to apply; see claimidx alternatives"
        return out
    out["manual"] = f"{kind} remedy; apply by hand: {body[:200]}"
    return out


def render_plan(p: dict[str, Any], *, trusted: bool, own: str, src: str) -> str:
    lines = [f"# apply {p['id']} ({p['kind']}) in {p['cwd']}"]
    if not trusted:
        lines.append(f"# this claim was not published here (own={own}, src={src}); its remedy is another agent's choice")
    if p.get("manual"):
        lines.append("manual: " + p["manual"])
        return "\n".join(lines)
    for step in p["steps"]:
        lines.append("$ " + shlex.join(step) + (" < fix.b" if p.get("stdin") and step[-1] == "-" else ""))
    lines.append("then: confirm --replay --cwd " + p["cwd"])
    return "\n".join(lines)


def run_plan(p: dict[str, Any]) -> dict[str, Any]:
    """Execute the plan's steps in order; stop at the first failure."""
    done: list[dict[str, Any]] = []
    for step in p["steps"]:
        try:
            proc = subprocess.run(step, cwd=p["cwd"], input=p.get("stdin"), capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            done.append({"argv": step, "rc": None, "error": str(e)})
            return {"ok": False, "steps": done}
        done.append({"argv": step, "rc": proc.returncode, "stderr": (proc.stderr or "")[-400:], "stdout": (proc.stdout or "")[-200:]})
        if proc.returncode != 0:
            return {"ok": False, "steps": done}
    return {"ok": True, "steps": done}


def apply_claim(store, claim: Claim, *, cwd: str, own: str | None, yes: bool, trust_eval: bool = False) -> dict[str, Any]:
    from .evaltrust import eval_trust

    trusted = eval_trust(store, claim) == "local"
    p = plan(claim, cwd)
    out: dict[str, Any] = {"id": claim.id, "plan": p, "trusted": trusted, "applied": False}
    if p.get("manual"):
        out["manual"] = p["manual"]
        return out
    if not yes:
        out["hint"] = "re-run with --yes to execute these steps and replay the eval"
        return out
    result = run_plan(p)
    out["run"] = result
    if not result["ok"]:
        out["error"] = "apply step failed; nothing was recorded"
        return out
    out["applied"] = True
    from .claim import _replay_now

    out["replay"] = _replay_now(claim.id, db=store.path, own=own, cwd=p["cwd"], trust_eval=trust_eval)
    if out["replay"].get("recorded"):
        # The hold is on record: the sensor's remembered failure for this tree or fingerprint is
        # consumed, as `claim --yes` does, so the next run or Stop hook does not ask the agent to
        # claim what was just recorded. A failure remembered elsewhere is left alone.
        from .env import forget_failure, last_failure

        rec = last_failure() or {}
        same_tree = os.path.abspath(str(rec.get("cwd") or "")) == p["cwd"] if rec.get("cwd") else False
        if rec and (same_tree or (rec.get("fp") and rec.get("fp") == claim.fp)):
            forget_failure()
    return out
