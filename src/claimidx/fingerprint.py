from __future__ import annotations

import hashlib
import re
from typing import Any

_PATH = re.compile(r"(?:[A-Za-z]:\\|\\\\|~[/\\]|/(?:home|Users|usr|var|tmp|opt|root|etc|app|src|private|opt)|(?:\./|\.\./))[^\s:'\"]+")
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
    ("module_not_found", re.compile(r"cannot find module|modulenotfounderror|no module named", re.I)),
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


def _quote_token(m: re.Match[str]) -> str:
    inner = m.group(1) if m.group(1) is not None else m.group(2)
    if inner and _MODULE_NAME.fullmatch(inner) and len(inner) < 80:
        return inner
    return "<STR>"


def normalization_risk(raw: str) -> list[str]:
    """What normalize_error erases that can distinguish two failures.

    Content-based: already-canonical queries that contain PLACEHOLDERS
    carry the same flags as a raw query that would produce them.
    """
    s = raw or ""
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
    s = raw.strip()
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


def classify(raw: str) -> str:
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


def fingerprint_material(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> str:
    nerr = normalize_error(err)
    cls = cls or classify(err)
    rt_major = re.sub(r"(\d+)\.\d+.*", r"\1", rt or "")
    deps = _canon_list(dep)
    return "\n".join([f"cls={cls}", f"err={nerr}", f"eco={(eco or '').lower()}", f"rt={rt_major.lower()}", f"dep={deps}"])


def fingerprint(*, err: str, cls: str = "", eco: str = "", rt: str = "", dep: list[str] | None = None) -> str:
    material = fingerprint_material(err=err, cls=cls, eco=eco, rt=rt, dep=dep)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


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
    return {
        "codes": list(dict.fromkeys(codes))[:8],
        "packages": list(dict.fromkeys(packages))[:8],
        "symbols": symbols,
        "normalization_risk": normalization_risk(raw),
    }


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
