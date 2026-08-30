"""Public projection of a local claim.

Local ingest keeps the full (secret-scanned) record. The public commons
only receives a twin that still matches (same fingerprint) but has no
notes, no local paths, no project eval recipes, no mailbox identifiers.

A private live home (CLAIMIDX_HOME_API you control) still gets the full
claim. The outbox / home-propose path — the GitHub ledger — always
projects. That is how the public library grows without shipping a
customer's tree.
"""

from __future__ import annotations

import re

from .fingerprint import normalize_error
from .models import Claim, EvalSpec, Fix
from .team import agent_slug

_EMAIL = re.compile(r"\b\S+@\S+\.\S+\b")
# Home/drive/tests/ paths only. A basename recipe (`python3 check.py`) is portable
# when fix.b is the script; a `.py` suffix is not a leak.
_EVAL_LOCAL = re.compile(
    r"(?:tests[/\\]|[A-Za-z]:[/\\]|/(?:home|Users)/|\\\\)",
    re.I,
)
_HOSTY = re.compile(
    r"\b(?:localhost|\.internal\b|\.local\b|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b",
    re.I,
)
# Home/tmp/drive paths only. Relative aliases like ./src/* are public recipe.
# Windows drive is C:\ or C:/foo — not l:// inside postgresql:// or https://.
_ABS_PATH = re.compile(
    r"(?:[A-Za-z]:(?:\\+|/(?!/))|\\\\|~[/\\]|/(?:home|Users|usr|var|tmp|root|etc)/)[^\s'\"]+",
    re.I,
)


class PublicSkip(ValueError):
    """This claim must not leave the machine."""


_TAUTOLOGY_CMD = re.compile(
    r"^(true|false|(?:python3?|node|go|cargo|rustc|npm|npx|docker|uv)(?:\.exe)?\s+(?:--version|-v|-V|version))\s*$",
    re.I,
)


def eval_is_proof(cmd: str | None) -> bool:
    """True when eval.cmd can discriminate held vs miss. `true` is a hint."""
    raw = (cmd or "").strip()
    if not raw:
        return False
    if _TAUTOLOGY_CMD.match(raw):
        return False
    head = raw.split()[0].lower()
    return head not in {"true", "false"}


def public_eval(cmd: str) -> str:
    """Portable recipe only. A tree path is not rewritten as `true` — that looked like proof."""
    raw = (cmd or "").strip()
    if not raw:
        return ""
    if _EVAL_LOCAL.search(raw) or _HOSTY.search(raw) or _EMAIL.search(raw):
        return ""
    return raw[:200]


def _pkg_token(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return ""
    if s.startswith("@"):
        body = s[1:]
        if "/" in body:
            scope, _, rest = body.partition("/")
            name, _, _ = rest.partition("@")
            name = name.split("[", 1)[0].strip()
            if scope and name:
                return "@" + scope + "/" + name
        return ""
    for sep in ("==", ">=", "<=", "~=", ">", "<", "@", "["):
        if sep in s:
            s = s.split(sep, 1)[0]
            break
    return s.strip()


def _pin_line(raw: str) -> str:
    s = (raw or "").strip().splitlines()[0] if raw else ""
    s = re.split(r"\s+#", s, maxsplit=1)[0].strip()
    return re.sub(
        r"^(?:pip3?|uv|python3?\s+-m\s+pip)\s+install\s+",
        "",
        s,
        count=1,
        flags=re.I,
    ).strip()


def _exact_pin(raw: str) -> tuple[str, str] | None:
    """Name + exact version, or None for a range / unversioned pin."""
    s = _pin_line(raw)
    if not s:
        return None
    if s.startswith("@"):
        m = re.fullmatch(r"(@[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([A-Za-z0-9_.+-]+)", s)
        return (m.group(1), m.group(2)) if m else None
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", s)
    if m:
        return m.group(1), m.group(2)
    m = re.fullmatch(r"([A-Za-z0-9_.-]+)@([A-Za-z0-9_.+-]+)", s)
    if m:
        return m.group(1), m.group(2)
    return None


def _version_triple(raw: str) -> tuple[int, int, int] | None:
    """Numeric X / X.Y / X.Y.Z only. Pre-releases and local tags are not a pin check."""
    s = (raw or "").strip()
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", s):
        return None
    parts = [int(p) for p in s.split(".")] + [0, 0]
    return parts[0], parts[1], parts[2]


def _range_pin(raw: str) -> tuple[str, list[tuple[str, tuple[int, int, int]]]] | None:
    """Name + numeric range clauses. None for exact, unversioned, or non-numeric specs."""
    s = _pin_line(raw)
    if not s or s.startswith("@"):
        return None
    m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?\s*(.*)$", s)
    if not m:
        return None
    name, rest = m.group(1), (m.group(2) or "").strip()
    if not rest:
        return None
    clauses: list[tuple[str, tuple[int, int, int]]] = []
    while rest:
        rest = rest.lstrip(",").strip()
        if not rest:
            break
        cm = re.match(r"(==|!=|<=|>=|<|>)\s*([A-Za-z0-9_.+-]+)", rest)
        if not cm:
            return None
        trip = _version_triple(cm.group(2))
        if trip is None:
            return None
        clauses.append((cm.group(1), trip))
        rest = rest[cm.end() :].strip()
    if not clauses:
        return None
    if len(clauses) == 1 and clauses[0][0] == "==":
        return None
    return name, clauses


def _py_range_eval(name: str, clauses: list[tuple[str, tuple[int, int, int]]]) -> str | None:
    """Stdlib-only interval check. Unparseable installed versions miss, they do not pass."""
    if not re.match(r"^[A-Za-z0-9._-]+$", name):
        return None
    conds = " and ".join(f"(v{op}{trip})" for op, trip in clauses)
    return (
        'python -c "from importlib.metadata import version as V;'
        "t=lambda s:tuple(int(x) for x in (s.split('.')+['0','0'])[:3]);"
        f"v=t(V({name!r}));raise SystemExit(not ({conds}))\""
    )


def refine_eval(
    cmd: str,
    *,
    fix_k: str = "",
    fix_b: str = "",
    dep: list[str] | None = None,
    eco: str = "",
) -> str:
    """If the agent passed a tautology, upgrade a pin into a portable import/require.

    An exact pin (`pkg==1.2.3` / `pkg@1.2.3`) must check that version, not
    merely that some build of `pkg` imports. A numeric range (`pkg>=1.2,<2`)
    must check the interval the same way. Non-numeric markers stay an
    import/require. Never refuses. Never invents a tree recipe. A remaining
    `true` is still a valid local hint.
    """
    raw = (cmd or "").strip() or "true"
    if eval_is_proof(raw):
        return raw
    pin_src = fix_b if (fix_k or "") == "pin" else ""
    exact = _exact_pin(pin_src) if pin_src else None
    if not exact and not _pkg_token(pin_src):
        for d in dep or []:
            exact = _exact_pin(d)
            if exact:
                break
    eco = (eco or "").lower()
    if exact:
        name, ver = exact
        if eco in {"npm", "node"} or name.startswith("@"):
            import json as _json

            req = _json.dumps(name + "/package.json")
            return f"node -e \"if(require({req}).version!=={_json.dumps(ver)}) process.exit(1)\""
        if re.match(r"^[A-Za-z0-9._-]+$", name):
            return (
                'python -c "from importlib.metadata import version; '
                f'raise SystemExit(version({name!r})!={ver!r})"'
            )
    rng = _range_pin(pin_src) if pin_src else None
    if not rng and not _pkg_token(pin_src):
        for d in dep or []:
            rng = _range_pin(d)
            if rng:
                break
    if rng and eco not in {"npm", "node"}:
        generated = _py_range_eval(rng[0], rng[1])
        if generated:
            return generated
    token = _pkg_token(pin_src) if pin_src else ""
    if not token:
        for d in dep or []:
            token = _pkg_token(d)
            if token:
                break
    if not token:
        return raw
    if eco in {"npm", "node"} or token.startswith("@"):
        import json as _json

        return "node -e \"require(" + _json.dumps(token) + ")\""
    mod = token.replace("-", "_")
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", mod):
        return f'python -c "import {mod}"'
    return raw


def public_tried(items: list[str] | None) -> list[str]:
    out: list[str] = []
    for t in items or []:
        slug = agent_slug(t)[:40]
        if slug in ("agent", "") or _EVAL_LOCAL.search(t) or "/" in t or "\\" in t:
            continue
        out.append(slug)
        if len(out) >= 8:
            break
    return out


def public_fix_body(text: str) -> str:
    """Redact mailbox, internal hosts, and absolute home paths.

    Do not run error-normalization here: that rewrites pins (`pydantic>=2.7,<3`
    → `pydantic>=<N>.<N>,<<N>`), flags (`--max-old-space-size=4096`), and
    relative paths (`./src/*`) that agents need to apply the fix.
    """
    s = (text or "").strip()
    s = _EMAIL.sub("<STR>", s)
    s = _HOSTY.sub("<HOST>", s)
    s = _ABS_PATH.sub("<PATH>", s)
    return s[:4000]


def project_public(claim: Claim) -> Claim:
    """Same id + fingerprint, fields safe for the commons."""
    body = public_fix_body(claim.fix.b)
    if not body.strip():
        raise PublicSkip("fix empty after public projection")
    data = claim.model_dump()
    data["err"] = normalize_error(claim.err)
    data["note"] = ""
    data["model"] = ""
    data["tried"] = public_tried(claim.tried)
    data["tool"] = public_tried(claim.tool)
    data["fix"] = Fix(k=claim.fix.k, b=body)
    data["eval"] = EvalSpec(cmd=public_eval(claim.eval.cmd), expect=claim.eval.expect)
    data["src"] = "home"
    return Claim.model_validate(data)
