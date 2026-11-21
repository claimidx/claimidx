from __future__ import annotations

import hashlib
import re
from typing import Any

from .security import redact_credentials

_PATH = re.compile(
    # Lookbehind keeps claimidx:// (and other letter-scheme URLs) from matching as C:/.
    r"(?:(?<![A-Za-z0-9])[A-Za-z]:[/\\]|\\\\|~[/\\]|/(?:home|Users|usr|var|tmp|opt|root|etc|app|src|private|workspace)|(?:\./|\.\./|\.\\|\.\.\\))[^\s:\'\"]+"
    r"|(?:(?<=\s)|^)/(?:[^\s:\'\"]+/)+(?:bin/python[\w.]*|bin/node|Scripts/python(?:\.exe)?)"
)
_URL = re.compile(r"https?://[^\s]+")
# Contractions (`Can't`) are not quotes. Single quotes only when not mid-word.
_QUOTED = re.compile(r"(?:(?<![A-Za-z])'([^']{1,200})'(?![A-Za-z])|\"([^\"]{1,200})\")")
_NUM = re.compile(r"\b\d+\b")
# Error codes discriminate failures (Errno 2 vs 13, HTTP 401 vs 429, exit 137 vs 1).
# Line numbers, counts, and versions still collapse to <N>.
_CODE_NUM = re.compile(r"(?i)\b(Errno|WinError|error code:?|exit code:?|status(?: code)?:?|HTTP(?:/[\d.]+)?)\s+(\d+)\b")
_CODE_MARK = "\x00"
_HEX = re.compile(r"\b[0-9a-f]{7,}\b", re.I)
_WS = re.compile(r"\s+")

_CLASS_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("async_api", re.compile(r"await|params (?:is|are) (?:a )?promise|searchparams (?:is|are) (?:a )?promise|sync.*async", re.I)),
    (
        "module_not_found",
        re.compile(
            r"cannot find module|modulenotfounderror|no module named"
            r"|no required module provides package|cannot find package"  # go
            r"|unresolved import|can't find crate|use of undeclared crate or module|cannot find (?:module or )?crate"  # rust
            r"|package [A-Za-z0-9_.]+ does not exist|could not find artifact|could not find [A-Za-z0-9_.-]+:[A-Za-z0-9_.-]+:",  # java
            re.I,
        ),
    ),
    ("schema_break", re.compile(r"pydantic|zod|validationerror|unexpected keyword", re.I)),
    ("lockfile_drift", re.compile(r"lockfile|peer dep|ERESOLVE|version conflict", re.I)),
    ("mcp_transport", re.compile(r"\bmcp\b|stdio transport|sse transport", re.I)),
    ("browser_dep", re.compile(r"playwright|puppeteer|chromium|webkit", re.I)),
    ("http_status_lie", re.compile(r"status(?: code)?\s*200|http 200", re.I)),
    ("http_429", re.compile(r"\b429\b|rate[- ]limit", re.I)),
    ("http_401", re.compile(r"\b401\b|unauthorized", re.I)),
    ("http_403", re.compile(r"\b403\b|forbidden", re.I)),
    ("auth_scope", re.compile(r"insufficient (?:scope|permission)|oauth", re.I)),
    ("import_error", re.compile(r"importerror|import error", re.I)),
    ("syntax", re.compile(r"syntaxerror|unexpected token", re.I)),
    ("perm", re.compile(r"eacces|permission denied", re.I)),
    ("ci_flake", re.compile(r"eagain|etimedout|flaky|not found in path", re.I)),
    ("type_error", re.compile(r"typeerror|type error", re.I)),
    # After existing rules so current classifications (including http_status_lie) do not move.
    (
        "write_unverified",
        re.compile(
            r"\b20[12]\b.*(?:already|duplicate|exists)|accepted but"
            r"|\b(?:http(?:\s+status)?|status(?:\s+code)?)\s*20[12]\b"
            r"|created but (?:unverified|not (?:found|visible|present))",
            re.I,
        ),
    ),
    (
        "stale_identical",
        re.compile(
            r"same result for different query|cached response|stale identical"
            r"|identical output for (?:a )?different",
            re.I,
        ),
    ),
]


_MODULE_NAME = re.compile(r"^[A-Za-z0-9_@./=-]+$")

# Closed vocabulary. Adding a token requires the scanner in the same commit.
PLACEHOLDERS: tuple[str, ...] = ("<STR>", "<URL>", "<PATH>", "<HEX>", "<N>")
_PLACEHOLDER_RISK = {
    "<STR>": "str",
    "<URL>": "url",
    "<PATH>": "path",
    "<HEX>": "hex",
    "<N>": "int",
}

# Closed catalog of known rule/error ID shapes. Adding a pattern requires a test
# in the same commit. Bare integers (exit 1, HTTP 200) never match.
# v0.7.24 catalog: kept so fingerprints() still accepts rows hashed under it.
_ID_SPECS_V0724: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ts", re.compile(r"\b(TS\d{3,5})\b")),
    ("ruff", re.compile(r"\b(RUF\d{3})\b")),
    ("rustc", re.compile(r"\berror\[(E\d{4})\]", re.I)),
    ("lint4", re.compile(r"\b([CRWEF]\d{4})\b")),
    ("lint3", re.compile(r"\b([EWF]\d{3})\b")),
)
# Current catalog: 0.7.24 plus Pylint/Ruff PL[CERW]#### and selected Ruff prefixes.
# ESLint rule names are matched separately (end of string, eco=npm only).
_ID_SPECS: tuple[tuple[str, re.Pattern[str]], ...] = _ID_SPECS_V0724 + (
    ("pylint", re.compile(r"\b(PL[CERW]\d{4})\b")),
    ("ruff_extra", re.compile(r"\b((?:ANN|ARG|SIM|UP|B)\d{3})\b")),
)
_ESLINT_ID = re.compile(r"(?<=\s)((?:@[\w-]+/)?[a-z]+(?:-[a-z]+)+)\s*$")

_RULE_ID_MAP: dict[str, str] | None = None


def _rule_id_map() -> dict[str, str]:
    """Flatten data/rule_id_map.json to {old_id: canonical_id}."""
    global _RULE_ID_MAP
    if _RULE_ID_MAP is not None:
        return _RULE_ID_MAP
    import json
    from pathlib import Path as _Path

    path = _Path(__file__).resolve().parent / "data" / "rule_id_map.json"
    flat: dict[str, str] = {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _RULE_ID_MAP = flat
        return flat
    if isinstance(raw, dict):
        for _tool, entries in raw.items():
            if not isinstance(entries, dict):
                continue
            for old_id, spec in entries.items():
                target = spec.get("to") if isinstance(spec, dict) else spec
                if isinstance(old_id, str) and isinstance(target, str) and target:
                    flat[old_id] = target
                    if old_id != old_id.upper():
                        flat[old_id.upper()] = target
    _RULE_ID_MAP = flat
    return flat


def remap_rule_id(token: str) -> str:
    """Apply the per-tool-version map. Unknown tokens are returned unchanged."""
    table = _rule_id_map()
    seen: set[str] = set()
    current = token
    while current not in seen:
        seen.add(current)
        nxt = table.get(current) or table.get(current.upper()) or table.get(current.lower())
        if not nxt or nxt == current:
            break
        current = nxt
    return current


def _quote_token(m: re.Match[str]) -> str:
    inner = m.group(1) if m.group(1) is not None else m.group(2)
    if inner and _MODULE_NAME.fullmatch(inner) and len(inner) < 80:
        return inner
    return "<STR>"


def normalization_risk(raw: str) -> list[str]:
    """What normalize_error erases that can distinguish two failures.

    Content-based: already-canonical queries that contain PLACEHOLDERS
    carry the same flags as a raw query that would produce them.
    Independent of canonical_error_id: an ID in the text does not drop these flags.
    """
    s = redact_credentials(raw or "")
    flags: list[str] = []
    if _URL.search(s):
        flags.append("url")
    if _PATH.search(s):
        flags.append("path")
    if _HEX.search(s):
        flags.append("hex")
    if _NUM.search(_CODE_NUM.sub(" ", s)):
        flags.append("int")
    if "str" not in flags:
        for m in _QUOTED.finditer(s):
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            if not (inner and _MODULE_NAME.fullmatch(inner) and len(inner) < 80):
                flags.append("str")
                break
    for tok, name in _PLACEHOLDER_RISK.items():
        if tok in s and name not in flags:
            flags.append(name)
    return flags


def normalize_error(raw: str) -> str:
    # Credentials are redacted before anything is hashed; text without one is unchanged.
    s = redact_credentials(raw).strip()
    s = _URL.sub("<URL>", s)
    s = _PATH.sub("<PATH>", s)
    s = _QUOTED.sub(_quote_token, s)
    s = _HEX.sub("<HEX>", s)
    kept: list[str] = []

    def _keep(m: re.Match[str]) -> str:
        kept.append(m.group(2))
        return f"{m.group(1)} {_CODE_MARK}K{len(kept) - 1}{_CODE_MARK}"

    s = _CODE_NUM.sub(_keep, s)
    s = _NUM.sub("<N>", s)
    s = re.sub(f"{_CODE_MARK}K(\\d+){_CODE_MARK}", lambda m: kept[int(m.group(1))], s)
    s = _WS.sub(" ", s)
    return s[:280]


def _best_id(
    raw: str,
    specs: tuple[tuple[str, re.Pattern[str]], ...],
    *,
    eco: str = "",
    allow_eslint: bool = True,
) -> tuple[int, str] | None:
    s = redact_credentials(raw or "")
    best: tuple[int, str] | None = None
    for _name, pat in specs:
        m = pat.search(s)
        if not m:
            continue
        token = m.group(1).upper()
        start = m.start()
        if best is None or start < best[0]:
            best = (start, token)
    if allow_eslint and (eco or "").lower() == "npm":
        m = _ESLINT_ID.search(s)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.group(1))
    return best


def canonical_error_id(raw: str, *, eco: str = "", apply_map: bool = True) -> str | None:
    """If a known rule or error ID appears in the text, return that canonical ID.

    The leftmost match wins. A reworded message that still carries the ID
    maps to the same fingerprint material. ESLint names (`no-unused-vars`,
    `@plugin/rule`) are recognized only at the end of the string when eco=npm.
    The packaged rule_id_map.json remaps deprecated tool IDs onto their
    current names when apply_map is true.
    """
    best = _best_id(raw, _ID_SPECS, eco=eco, allow_eslint=True)
    if not best:
        return None
    token = best[1]
    return remap_rule_id(token) if apply_map else token


def _error_material(
    raw: str,
    *,
    eco: str = "",
    apply_map: bool = True,
    specs: tuple[tuple[str, re.Pattern[str]], ...] | None = None,
) -> str:
    if specs is None:
        cid = canonical_error_id(raw, eco=eco, apply_map=apply_map)
    else:
        best = _best_id(raw, specs, eco=eco, allow_eslint=False)
        cid = (remap_rule_id(best[1]) if apply_map else best[1]) if best else None
    if cid:
        return f"id={cid}"
    return normalize_error(raw)


def classify(raw: str) -> str:
    raw = redact_credentials(raw or "")
    for name, pat in _CLASS_RULES:
        if pat.search(raw):
            return name
    return "other"


def _canon_list(items: list[str] | None) -> str:
    return ",".join(sorted({i.strip().lower() for i in (items or []) if i.strip()}))


def runtime_proof_key(rt: str) -> str:
    """Grain at which a hold is keyed.

    Fingerprint still collapses Python to major (`py@3`). Holds do not:
    Python is major.minor (`py@3.12`), Node is major (`node@20`).
    """
    s = (rt or "").strip().lower()
    if not s:
        return ""
    m = re.match(r"^(?:py|python)@(\d+)(?:\.(\d+))?", s)
    if m:
        major, minor = m.group(1), m.group(2)
        if minor is None:
            return f"py@{major}"
        return f"py@{major}.{minor}"
    m = re.match(r"^(?:node|nodejs)@(\d+)", s)
    if m:
        return f"node@{m.group(1)}"
    return s


def _join_material(*, nerr: str, cls: str, eco: str, rt: str, dep: list[str] | None, raw: str) -> str:
    # A known ID is the identity of the failure: class is taken from the wording
    # and would split a reworded message, so it is not part of the hash.
    cls = "id" if nerr.startswith("id=") else (cls or classify(raw))
    rt_major = re.sub(r"(\d+)\.\d+.*", r"\1", rt or "")
    deps = _canon_list(dep)
    # An absent ecosystem hashes as "other": that is what the store and the ledger write, so a row
    # published without --eco recomputes its own fp on every pull.
    return "\n".join([f"cls={cls}", f"err={nerr}", f"eco={(eco or 'other').lower()}", f"rt={rt_major.lower()}", f"dep={deps}"])


def fingerprint_material(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> str:
    return _join_material(nerr=_error_material(err, eco=eco, apply_map=True), cls=cls, eco=eco, rt=rt, dep=dep, raw=err)


def legacy_fingerprint_material(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> str:
    """Pre-0.7.24 material: the normalized wording, even when a rule/error ID is present."""
    return _join_material(nerr=normalize_error(err), cls=cls, eco=eco, rt=rt, dep=dep, raw=err)


def v0724_fingerprint_material(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> str:
    """0.7.24 catalog: known TS/RUF/rustc/lint IDs only, no remap, no ESLint/PL* shapes."""
    return _join_material(
        nerr=_error_material(err, eco=eco, apply_map=False, specs=_ID_SPECS_V0724),
        cls=cls,
        eco=eco,
        rt=rt,
        dep=dep,
        raw=err,
    )


def unmapped_fingerprint_material(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> str:
    """Current catalog before rule_id_map.json is applied."""
    return _join_material(nerr=_error_material(err, eco=eco, apply_map=False), cls=cls, eco=eco, rt=rt, dep=dep, raw=err)


def _hash_material(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def fingerprint(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> str:
    """Current hash. New writes use this. When a known ID is in the text, the hash is the ID."""
    return _hash_material(fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep))


def fingerprints(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> tuple[str, ...]:
    """Current, legacy (pre-0.7.24 wording), and remapped (pre-map / 0.7.24 ID) hashes.

    Unique values, in that order. Pull and exact-match accept any of them, so a
    stored fp is never rewritten. New writes use fingerprint() (the first value).
    """
    current = fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep)
    legacy = _hash_material(legacy_fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep))
    remapped = _hash_material(unmapped_fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep))
    mid = _hash_material(v0724_fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep))
    out: list[str] = []
    for fp in (current, legacy, remapped, mid):
        if fp not in out:
            out.append(fp)
    return tuple(out)


def fingerprint_algorithms(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> list[tuple[str, str]]:
    """(fp, algo) pairs for fp_alias edges: current, legacy, remapped, v0724."""
    pairs = [
        (_hash_material(fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep)), "current"),
        (_hash_material(legacy_fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep)), "legacy"),
        (_hash_material(unmapped_fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep)), "remapped"),
        (_hash_material(v0724_fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep)), "v0724"),
    ]
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for fp, algo in pairs:
        if fp not in seen:
            seen.add(fp)
            out.append((fp, algo))
    return out


def fingerprint_accepted(fp: str, *, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> bool:
    """True when `fp` is the current, legacy, remapped, or 0.7.24 hash of these fields."""
    return (fp or "") in fingerprints(err=err, cls=cls, eco=eco, rt=rt, dep=dep)


def same_fingerprint(
    *,
    query_fp: str = "",
    query_err: str = "",
    query_cls: str = "",
    query_eco: str = "",
    query_rt: str = "",
    query_dep: list[str] | None = None,
    claim_fp: str = "",
    claim_err: str = "",
    claim_cls: str = "",
    claim_eco: str = "",
    claim_rt: str = "",
    claim_dep: list[str] | None = None,
) -> bool:
    """Exact match under the current, legacy, or remapped algorithm, including a reworded ID."""
    q_fps = fingerprints(err=query_err, cls=query_cls, eco=query_eco, rt=query_rt, dep=query_dep)
    if query_fp and query_fp not in q_fps:
        q_fps = (query_fp, *q_fps)
    if claim_fp and claim_fp in q_fps:
        return True
    c_fps = fingerprints(err=claim_err, cls=claim_cls, eco=claim_eco, rt=claim_rt, dep=claim_dep)
    if claim_fp and claim_fp not in c_fps:
        c_fps = (claim_fp, *c_fps)
    return bool(set(q_fps) & set(c_fps))


_SYMBOL = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]{2,79}\b")
_PACKAGE = re.compile(r"(?:no module named|cannot find module|package)\s+['\"]?([@A-Za-z0-9_./-]+)", re.I)


def error_features(raw: str) -> dict[str, Any]:
    """Extract additive v2 matching features without changing fingerprint v1."""
    normalized = normalize_error(raw)
    codes = [m.group(2) for m in _CODE_NUM.finditer(raw)]
    packages = [m.group(1).lower() for m in _PACKAGE.finditer(raw)]
    symbols = sorted(
        {token.lower() for token in _SYMBOL.findall(normalized) if token.lower() not in {"error", "exception", "traceback", "typeerror", "runtimeerror"}}
    )[:24]
    cid = canonical_error_id(raw)
    out: dict[str, Any] = {
        "codes": list(dict.fromkeys(codes))[:8],
        "packages": list(dict.fromkeys(packages))[:8],
        "symbols": symbols,
        "normalization_risk": normalization_risk(raw),
    }
    if cid:
        out["error_id"] = cid
    return out


def family_fingerprint(*, err: str, cls: str = "", eco: str = "") -> str:
    """A broad, versioned family key.  It complements and never replaces fp v1."""
    features = error_features(err)
    material = "\n".join(
        [
            "family=1",
            f"cls={cls or classify(err)}",
            f"eco={(eco or '').lower()}",
            "codes=" + ",".join(features["codes"]),
            "packages=" + ",".join(features["packages"]),
            "symbols=" + ",".join(features["symbols"][:12]),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
