from __future__ import annotations

import hashlib
import re

_PATH = re.compile(
    r"(?:[A-Za-z]:\\|\\\\|~[/\\]|/(?:home|Users|usr|var|tmp|opt|root|etc|app|src|private|opt)|(?:\./|\.\./))[^\s:'\"]+"
)
_URL = re.compile(r"https?://[^\s]+")
# Contractions (`Can't`) are not quotes. Single quotes only when not mid-word.
_QUOTED = re.compile(
    r"(?:(?<![A-Za-z])'([^']{1,200})'(?![A-Za-z])|\"([^\"]{1,200})\")"
)
_NUM = re.compile(r"\b\d+\b")
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


def _quote_token(m: re.Match[str]) -> str:
    inner = m.group(1) if m.group(1) is not None else m.group(2)
    if inner and _MODULE_NAME.fullmatch(inner) and len(inner) < 80:
        return inner
    return "<STR>"


def normalization_risk(raw: str) -> list[str]:
    """What normalize_error erases that can distinguish two failures.

    earnest-penny: a false exact fingerprint can rank before replay.
    """
    s = raw or ""
    flags: list[str] = []
    if _URL.search(s):
        flags.append("url")
    if _PATH.search(s):
        flags.append("path")
    if _HEX.search(s):
        flags.append("hex")
    if _NUM.search(s):
        flags.append("int")
    for m in _QUOTED.finditer(s):
        inner = m.group(1) if m.group(1) is not None else m.group(2)
        if not (inner and _MODULE_NAME.fullmatch(inner) and len(inner) < 80):
            flags.append("str")
            break
    return flags


def normalize_error(raw: str) -> str:
    s = raw.strip()
    s = _URL.sub("<URL>", s)
    s = _PATH.sub("<PATH>", s)
    s = _QUOTED.sub(_quote_token, s)
    s = _HEX.sub("<HEX>", s)
    s = _NUM.sub("<N>", s)
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
