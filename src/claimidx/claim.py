"""`claimidx claim`: the near-zero-flag path from "I just fixed it" to a claim.

Everything `publish` asks for is drafted from what the machine already knows:
the error comes from the last hook-captured failure, eco/rt from the tree and
interpreter, dep from the installed version of the claimed target, the eval
from the claim class, and the fix from `--fix` text, the working-tree diff,
or the obvious install command. The agent reviews one JSON blob and says yes.

Nothing here weakens the gate: a drafted eval that cannot prove the claim is
still a hint, and the draft says so before anything is written.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from .env import infer_env, installed_version, last_failure, tree_eval
from .fingerprint import classify, fingerprint
from .public import eval_is_proof, ingest_warnings, refine_eval
from .target import claim_target, suggest_eval

_INSTALL_HEAD = re.compile(r"^(?:pip3?|uv pip|uv|poetry|pipx|npm|pnpm|yarn|bun|cargo|go|gem|composer)\s+(?:install|add|get|i)\b", re.I)
_CONFIG_HINT = re.compile(r"\b(?:export|set|setx)\s+[A-Z_]+=|\.env\b|\.(?:toml|ya?ml|json|ini|cfg)\b|config", re.I)
_PIN_HINT = re.compile(r"(?:==|~=|>=|<=|@\d)")
_DIFF_LIMIT = 3900  # Fix.b caps at 4000; a diff that fits is applied verbatim by `claimidx apply`


_INSTALL_SPEC = re.compile(r"^(?:pip3?|uv pip|uv|poetry|pipx|npm|pnpm|yarn|bun|cargo|go|gem|composer)\s+(?:install|add|get|i)\s+(?:-[-\w]+\s+)*([^\s]+)", re.I)


def normalize_fix(fix_b: str, fix_k: str = "") -> tuple[str, str]:
    """`pip install foo==1.2` is a pin, not a network command the policy will deny.

    Returns (fix_k, fix_b). Install commands collapse to their package spec:
    versioned -> pin, bare -> constraint. Everything else passes through.
    """
    raw = fix_b or ""
    if raw.lstrip().startswith("diff --git"):
        # A patch is bytes for `git apply`: keep every context line and the final newline.
        body = raw.lstrip()
        return "patch", body if body.endswith("\n") else body + "\n"
    s = raw.strip()
    m = _INSTALL_SPEC.match(s)
    if m and fix_k in ("", "cmd", "pin", "constraint"):
        spec = m.group(1).strip("'\"")
        return ("pin" if _PIN_HINT.search(spec) else "constraint"), spec
    return fix_k or infer_fix_kind(s), s


def infer_fix_kind(fix_b: str) -> str:
    """Best-effort fix.k from fix.b text. `patch` when nothing else fits."""
    s = (fix_b or "").strip()
    if not s:
        return "constraint"
    if _INSTALL_HEAD.match(s):
        return "pin" if _PIN_HINT.search(s) else "constraint"
    if _PIN_HINT.search(s) and len(s.split()) <= 3:
        return "pin"
    if s.startswith("diff --git") or s.startswith("---") or "@@" in s:
        return "patch"
    if _CONFIG_HINT.search(s):
        return "config"
    if s.lower().startswith(("won't fix", "wontfix", "not a bug")):
        return "wontfix"
    return "patch"


_DIFF_EXCLUDE = [
    ":(exclude,glob)**/.env*",
    ":(exclude,glob)**/*.pem",
    ":(exclude,glob)**/*.key",
    ":(exclude,glob)**/*secret*",
    ":(exclude,glob)**/*credential*",
    ":(exclude,glob)**/*.p12",
    ":(exclude,glob)**/*.pfx",
]


def _git_diff(cwd: str) -> str:
    """Working-tree diff, stat first, truncated to fit fix.b. Empty when no repo or no changes.

    Secret-shaped files are excluded by path, and if the remaining hunks still
    trip the secret scan only the stat survives: a claim is public by intent.
    """
    from .security import SecretError, reject_secrets, strip_control

    try:
        stat = subprocess.run(["git", "diff", "--stat", "--", ".", *_DIFF_EXCLUDE], cwd=cwd, capture_output=True, text=True, timeout=10, check=False)
        if stat.returncode != 0 or not (stat.stdout or "").strip():
            return ""
        full = subprocess.run(["git", "diff", "--no-color", "--", ".", *_DIFF_EXCLUDE], cwd=cwd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    stat_text = strip_control((stat.stdout or "").strip())
    hunks = strip_control(full.stdout or "")
    try:
        reject_secrets(hunks)
    except SecretError:
        return stat_text + "\n(diff hunks withheld: secret-shaped token; describe the change with --fix)"
    if len(hunks) <= _DIFF_LIMIT:
        return hunks  # exactly what `git apply` takes; the stat is derivable
    return stat_text + "\n" + hunks[:_DIFF_LIMIT].rstrip() + "\n... (truncated: too large to apply from fix.b; describe the change with --fix)"


_DIFF_MODULE = re.compile(
    r"^[+-](?!\+\+|--).*?\b(?:import\s+([A-Za-z_][A-Za-z0-9_]*)|from\s+([A-Za-z_][A-Za-z0-9_]*)|([A-Za-z_][A-Za-z0-9_]*)\.[A-Za-z_])", re.M
)


def deps_from_diff(diff: str, cwd: str, eco: str = "") -> list[str]:
    """Third-party packages the changed lines touch, as installed `name@ver` pins.

    `yaml.load` -> `yaml.safe_load` is a claim about PyYAML's API; the version
    is context the next agent needs for a match. Import names map to
    distributions through the tree's own interpreter.
    """
    if not diff or eco not in ("", "py"):
        return []
    names: list[str] = []
    for m in _DIFF_MODULE.finditer(diff):
        tok = next((g for g in m.groups() if g), "")
        if (
            tok
            and tok not in names
            and tok
            not in {
                "self",
                "cls",
                "os",
                "sys",
                "re",
                "json",
                "typing",
                "pathlib",
                "datetime",
                "logging",
                "math",
                "time",
                "io",
                "collections",
                "functools",
                "itertools",
                "subprocess",
                "unittest",
                "pytest",
            }
        ):
            names.append(tok)
    if not names:
        return []
    from .sandbox import project_python

    py = project_python(cwd) or None
    code = (
        "import json,sys\nfrom importlib.metadata import packages_distributions, version\n"
        "pd=packages_distributions(); out={}\n"
        "for n in sys.argv[1:]:\n"
        "    for d in pd.get(n) or []:\n"
        "        try: out[n]=d+'@'+version(d)\n"
        "        except Exception: pass\n"
        "print(json.dumps(out))"
    )
    try:
        proc = subprocess.run([py or "python", "-c", code, *names[:12]], capture_output=True, text=True, timeout=20, check=False, cwd=cwd)
        found = json.loads(proc.stdout or "{}") if proc.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    pins = [v for v in found.values() if not v.lower().startswith(("pip@", "setuptools@", "wheel@"))]
    return sorted(set(pins))[:3]


def _install_fix(target: str, eco: str, dep: list[str], installed: str = "") -> tuple[str, str]:
    """(fix_k, fix_b): the pin for the claimed target when its version is known, else a bare constraint.

    `installed` is the `Dist@ver` the tree reports for the target; its name wins
    over the import name (`yaml` -> `PyYAML==6.0.3`) because pip installs
    distributions, not modules.
    """
    name = target if target.startswith("@") else target.split(".")[0].split("/")[0]
    pin = installed or next((d for d in dep if d.lower().startswith(name.lower() + "@")), "")
    if pin and "@" in pin.lstrip("@"):
        head, _, ver = pin.rpartition("@")
        name, ver = (head or name), ver
    else:
        ver = ""
    if eco in {"npm", "node"} or target.startswith("@"):
        return ("pin", f"{name}@{ver}") if ver else ("constraint", name)
    return ("pin", f"{name}=={ver}") if ver else ("constraint", name)


def draft_claim(
    *,
    err: str = "",
    fix: str = "",
    fix_k: str = "",
    eval_cmd: str = "",
    eco: str = "",
    rt: str = "",
    dep: list[str] | None = None,
    cwd: str = "",
    note: str = "",
    use_diff: bool = True,
) -> dict[str, Any]:
    """Assemble a publish-ready claim from as little as the agent gave us. Never writes."""
    remembered = None
    if not (err or "").strip():
        remembered = last_failure()
        if not remembered:
            return {
                "ok": False,
                "error": "no failure to claim: pass --err, or run with the hook installed so the last failure is remembered",
            }
        err = str(remembered["err"])
        cwd = cwd or str(remembered.get("cwd") or "")
        eco = eco or str(remembered.get("eco") or "")
        rt = rt or str(remembered.get("rt") or "")
    err = re.sub(r"^E\s+", "", err.strip())  # pytest's E-prefix is not part of the error
    cwd = cwd or os.getcwd()
    guess = infer_env(cwd, err=err)
    eco = eco or guess["eco"] or "other"
    rt = rt or (guess["rt"] if guess["eco"] in ("", eco) else "")
    cls = classify(err)
    target = claim_target(cls=cls, err=err, dep=dep)
    dep = list(dep or [])
    inferred: dict[str, str] = {}
    if remembered:
        inferred["err"] = "last failure" + (f" ({remembered.get('command')})" if remembered.get("command") else "")
    if guess["eco"] and eco == guess["eco"]:
        inferred["eco"] = "tree/err"
    if guess["rt"] and rt == guess["rt"]:
        inferred["rt"] = "interpreter"
    installed = ""
    if target:
        installed = installed_version(target.split(".")[0].split("/")[0] if not target.startswith("@") else target, eco, cwd)
    if installed and not dep and cls != "module_not_found":
        # For a missing module the package was absent when it failed: the version is the fix (below), not the context.
        dep = [installed]
        inferred["dep"] = "installed"

    fix_b = (fix or "").strip()
    if not fix_b and use_diff:
        fix_b = _git_diff(cwd)
        if fix_b:
            inferred["fix_b"] = "git diff"
            fix_k = fix_k or "patch"
            if not dep and cls != "module_not_found":
                dep = deps_from_diff(fix_b, cwd, eco)
                if dep:
                    inferred["dep"] = "packages touched by the diff"
    if not fix_b and target:
        k, fix_b = _install_fix(target, eco, dep, installed=installed)
        fix_k = fix_k or k
        inferred["fix_b"] = "pin for claimed target" + (" (installed version)" if installed else "")
    if fix and not fix_k:
        inferred["fix_k"] = "from fix text"
    fix_k, fix_b = normalize_fix(fix_b, fix_k)

    ev = (eval_cmd or "").strip()
    if not ev and target:
        ev = suggest_eval(target, eco)
        if ev:
            inferred["eval"] = "claim target"
    if not ev:
        ev = tree_eval(cwd, eco)
        if ev:
            inferred["eval"] = "tree recipe"
    if not ev and fix_k in {"pin", "constraint"}:
        # Only a pin fix is proven by a version check; for a patch that would be a tautology.
        ev = refine_eval("true", fix_k=fix_k, fix_b=fix_b, dep=dep, eco=eco)
        if ev != "true":
            inferred["eval"] = "dependency pin"
    ev = ev or "true"
    if not rt:
        # The eval's own head decides the runtime a hold is keyed to, whatever the tree looked like.
        from .env import _node_rt, _py_rt
        from .policy import _norm_head

        head = _norm_head(ev.split()[0]) if ev.split() else ""
        if head in {"python", "python3", "pytest", "uv"}:
            rt = _py_rt()
            inferred["rt"] = "eval interpreter"
        elif head in {"node", "npx", "npm"}:
            rt = _node_rt()
            if rt:
                inferred["rt"] = "eval interpreter"

    warns = ingest_warnings(err, ev, cls=cls, dep=dep, eco=eco)
    if not fix_b:
        warns.insert(0, "fix.b empty: pass --fix '<what you changed>'")
    draft = {
        "ok": True,
        "err": err[:280],
        "cls": cls,
        "eco": eco,
        "rt": rt,
        "dep": dep,
        "fix_k": fix_k,
        "fix_b": fix_b,
        "eval": ev,
        "eval_proof": eval_is_proof(ev),
        "target": target,
        "note": note or "",
        "cwd": cwd,
        "fp": fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep),
        "inferred": inferred,
        "warn": warns,
        "publish_argv": _publish_argv(err, fix_k, fix_b, ev, eco, rt, dep, note),
    }
    return draft


def _publish_argv(err: str, fix_k: str, fix_b: str, ev: str, eco: str, rt: str, dep: list[str], note: str) -> str:
    parts = ["claimidx", "publish", "--err", err, "--fix-k", fix_k, "--fix-b", fix_b or "<fix>", "--eval", ev, "--eco", eco]
    if rt:
        parts += ["--rt", rt]
    for d in dep:
        parts += ["--dep", d]
    if note:
        parts += ["--note", note]
    return shlex.join(parts)


def publish_draft(draft: dict[str, Any], *, db: str | os.PathLike[str] | None, own: str | None = None, replay: bool = True) -> dict[str, Any]:
    """Write the draft as a claim; replay its eval under cwd so a held proof mints nr on the spot."""
    from .query import ingest

    if not draft.get("ok"):
        return dict(draft)
    if not (draft.get("fix_b") or "").strip():
        return {"ok": False, "error": "fix.b empty: pass --fix '<what you changed>'", "draft": draft}
    out = ingest(
        str(draft["err"]),
        fix_k=str(draft["fix_k"]),
        fix_b=str(draft["fix_b"]),
        eval=str(draft["eval"]),
        eco=str(draft.get("eco") or "other"),
        rt=str(draft.get("rt") or ""),
        dep=list(draft.get("dep") or []),
        note=str(draft.get("note") or ""),
        own=own,
        db=db,
        cwd=str(draft.get("cwd") or "") or None,
        observe_digest=True,
    )
    if out.get("exists") and str(out.get("st") or "") == "rejected":
        # The fingerprint's only claim was rejected: this draft is the correction, not a duplicate.
        # A live claim (proposed/confirmed) is still never overwritten from here; that is `publish --force`.
        out = ingest(
            str(draft["err"]),
            fix_k=str(draft["fix_k"]),
            fix_b=str(draft["fix_b"]),
            eval=str(draft["eval"]),
            eco=str(draft.get("eco") or "other"),
            rt=str(draft.get("rt") or ""),
            dep=list(draft.get("dep") or []),
            note=str(draft.get("note") or ""),
            own=own,
            db=db,
            cwd=str(draft.get("cwd") or "") or None,
            observe_digest=True,
            force=True,
        )
        out = dict(out)
        out["superseded"] = "rejected"
    out = dict(out)
    out["ok"] = True
    if out.get("exists"):
        return out
    if replay and draft.get("eval_proof"):
        out["replay"] = _replay_now(out["id"], db=db, own=own, cwd=str(draft.get("cwd") or ""))
    from .env import forget_failure

    forget_failure()
    return out


def _replay_now(claim_id: str, *, db, own: str | None, cwd: str, trust_eval: bool = False) -> dict[str, Any]:
    from .evaltrust import eval_trust
    from .gate import graduation_gate
    from .sandbox import replay
    from .store import DEFAULT_DB, Store
    from .team import resolve_owner

    store = Store(db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB))
    c = store.get(claim_id)
    if not c:
        return {"held": False, "recorded": False, "reason": "missing"}
    result = replay(c.eval.cmd, c.eval.expect, cwd=cwd or None, trust=eval_trust(store, c, override=trust_eval))
    info = result.as_dict()
    if result.is_hint() or not result.ran:
        from .gate import hint_refusal

        return {"held": False, "recorded": False, **hint_refusal(c, result, cwd=cwd), "replay": info}
    if not result.held:
        from .gate import unapplied_refusal

        unapplied = unapplied_refusal(c, result, cwd=cwd or None)
        if unapplied:
            return {"held": False, "recorded": False, **unapplied, "replay": info}
        # The agent says it is fixed; the eval disagrees. Record nothing, say so loudly.
        return {"held": False, "recorded": False, "reason": "eval-miss: the fix did not hold under this eval", "replay": info}
    decision = graduation_gate(c, result, cwd=cwd or None, store=store, actor=resolve_owner(own))
    if not decision.mint_nr:
        return {"held": True, "recorded": False, **decision.refusal(), "replay": info}
    detail = {"ms": int(result.ms or 0), "held": True, "env": {"rt": result.env} if result.env else {}}
    confirmed = store.confirm(claim_id, resolve_owner(own), replayed=True, detail=detail)
    from .home import maybe_share, share_observation

    shared = maybe_share(store, confirmed)
    if (shared or {}).get("status") in {"already", "pushed"}:
        shared = share_observation(store, confirmed, held=True, actor=resolve_owner(own)) or shared
    out: dict[str, Any] = {"held": True, "recorded": True, "nr": confirmed.nr, "st": confirmed.st, "replay": info}
    if decision.warns:
        out["warn"] = list(decision.warns)
    if shared:
        out["share"] = shared
    return out


def render_draft(draft: dict[str, Any]) -> str:
    if not draft.get("ok"):
        return f"error: {draft.get('error')}"
    lines = [
        f"err     {draft['err']}",
        f"cls     {draft['cls']}   eco {draft['eco']}   rt {draft['rt'] or '-'}   dep {','.join(draft['dep']) or '-'}",
        f"fix.k   {draft['fix_k']}",
        "fix.b   " + (draft["fix_b"].splitlines()[0] if draft["fix_b"] else "<empty>") + ("  ..." if draft["fix_b"].count("\n") else ""),
        f"eval    {draft['eval']}   proof={str(draft['eval_proof']).lower()}",
    ]
    if draft.get("inferred"):
        lines.append("drafted " + ", ".join(f"{k}<-{v}" for k, v in draft["inferred"].items()))
    for w in draft.get("warn") or []:
        lines.append(f"warn    {w}")
    lines.append("publish with: claimidx claim --yes   (or edit: " + draft["publish_argv"][:160] + ")")
    return "\n".join(lines)


def dumps(draft: dict[str, Any]) -> str:
    return json.dumps(draft, default=str)


__all__ = ["draft_claim", "publish_draft", "render_draft", "infer_fix_kind", "Path"]
