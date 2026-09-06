"""Target attribution: what a claim is about, and whether its eval looks at it.

A zero-exit is not proof unless the eval observes the thing the claim names.
`claim_target` extracts that thing for classes where it is unambiguous
(a missing module, a named dependency). For everything else it returns ""
and the gate does not judge — no heuristics for `other`.
"""

from __future__ import annotations

import re

_NO_MODULE = re.compile(
    r"(?:no module named|cannot find module|module not found:?)\s*['\"]?([@A-Za-z0-9_./-]+)['\"]?",
    re.I,
)
_TARGET_CLASSES = {"module_not_found", "browser_dep", "lockfile_drift"}
_WORD = re.compile(r"[A-Za-z0-9_@./-]+")


def _dep_name(pin: str) -> str:
    from .public import _pkg_token

    return _pkg_token(pin)


def claim_target(*, cls: str, err: str, dep: list[str] | None = None) -> str:
    """Name of the module/package this claim is about, or "" when not extractable."""
    if cls not in _TARGET_CLASSES:
        return ""
    if cls == "module_not_found":
        m = _NO_MODULE.search(err or "")
        if m:
            name = m.group(1).strip().rstrip(".")
            # `./lib`, `../x`, `/abs/path`: a missing local file, not a dependency. Nothing to attribute.
            if name.startswith((".", "/")) or ":" in name or "<" in name:
                return ""
            return name
    for d in dep or []:
        name = _dep_name(d)
        if name:
            return name
    return ""


def _variants(target: str) -> set[str]:
    t = target.strip().lower()
    out = {t}
    # yaml.loader -> yaml ; next/navigation -> next ; @scope/pkg stays whole and also scope-less
    if "." in t:
        out.add(t.split(".", 1)[0])
    if "/" in t:
        head = t.split("/", 1)[0]
        if not head.startswith("@"):
            out.add(head)
    out |= {v.replace("-", "_") for v in list(out)} | {v.replace("_", "-") for v in list(out)}
    return {v for v in out if v}


def eval_observes_target(cmd: str, target: str) -> bool:
    """True when the eval text names the target (or its top-level package)."""
    if not target:
        return True
    words = {w.lower() for w in _WORD.findall(cmd or "")}
    words |= {w.lower().replace("-", "_") for w in words}
    return bool(_variants(target) & words)


def proof_observes_target(steps: list[dict] | None, target: str) -> bool:
    """A v2 proof observes the target when an `expect_package` step names it."""
    if not target:
        return True
    want = _variants(target)
    for step in steps or []:
        if step.get("op") == "expect_package" and (step.get("package") or "").lower() in want:
            return True
        if step.get("op") == "run":
            blob = " ".join([str(step.get("program") or ""), *[str(a) for a in step.get("args") or []]])
            if eval_observes_target(blob, target):
                return True
    return False


def suggest_eval(target: str, eco: str = "") -> str:
    """The smallest eval that observes `target` in this ecosystem."""
    if not target:
        return ""
    eco = (eco or "").lower()
    if eco in {"npm", "node"} or target.startswith("@") or "/" in target:
        return "node -e \"require('" + target.replace("'", "") + "')\""
    mod = target.split("/")[0].replace("-", "_")
    if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*$", mod):
        return f'python -c "import {mod}"'
    return ""
