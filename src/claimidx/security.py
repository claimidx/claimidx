from __future__ import annotations

import re

# Credentials inside a URL: scheme://user:pass@host or scheme://:token@host (redis, postgres, mysql, mongodb,
# amqp, http(s), claimidx, ...). Only userinfo with a colon is a credential; ssh://git@host is not.
_CRED_URL = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^\s/@:'\"<>]*:[^\s/@'\"<>]+@")
# password=/token= key-value forms. Values that are placeholders or env references ($X, %X%, <X>, {x}) stay.
_CRED_KV = re.compile(
    r"(?i)\b(password|passwd|pwd|pass|auth_token|access_token|refresh_token|token|secret|client_secret|api[_-]?key)"
    r"(\s*=\s*)(['\"]?)(?![$%<{])([^\s'\"&,;\\]+)"
)
CRED_MARK = "<CRED>"
SECRET_MARK = "<SECRET>"

_SECRET_PATTERNS = [
    _CRED_URL,
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"sk-proj-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"api[_-]?key\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}", re.I),
    re.compile(r"secret\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{16,}", re.I),
    re.compile(r"password\s*[:=]\s*['\"]?[^\s'\"]{8,}", re.I),
    # A token, not the auth scheme. `WWW-Authenticate: Bearer realm=` must be writable.
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{16,}={0,2}", re.I),
]

# Documented public defaults, not secrets. JDK cacerts password is `changeit`.
_PUBLIC_DEFAULTS = [
    re.compile(r"(?i)-storepass\s+changeit\b"),
    re.compile(r"(?i)-keypass\s+changeit\b"),
    re.compile(r"(?i)trustStorePassword[\s\"'=:]+changeit\b"),
]


def redact_credentials(text: str | None) -> str:
    """Replace credential URLs and password/token key=value values with markers.

    Runs before fingerprinting and before anything is stored or shown. Text with
    no credential is returned unchanged, so existing fingerprints do not move.
    """
    if not text:
        return text or ""
    out = _CRED_URL.sub(lambda m: m.group(1) + CRED_MARK + "@", text)
    return _CRED_KV.sub(lambda m: m.group(1) + m.group(2) + m.group(3) + SECRET_MARK, out)


def _keep_match(hit: str) -> bool:
    return SECRET_MARK in hit or CRED_MARK in hit or any(d.search(hit) for d in _PUBLIC_DEFAULTS)


def redact(text: str | None) -> str:
    """redact_credentials, then every secret-shaped token, for context lines (stderr tails, outputs)."""
    out = redact_credentials(text)
    if not out:
        return out
    for pat in _SECRET_PATTERNS:
        if pat is _CRED_URL:
            continue
        out = pat.sub(lambda m: m.group(0) if _keep_match(m.group(0)) else SECRET_MARK, out)
    return out


def redact_obj(obj):
    """redact() every string in a JSON-shaped value (dict/list/str). Other values pass through."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


class SecretError(ValueError):
    pass


_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|\x9b[0-9;?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u200b-\u200f\u2028\u2029\u202a-\u202e\u2066-\u2069]")


def strip_control(text: str | None) -> str:
    """Drop ANSI escapes, C0 controls, and bidi/zero-width characters.

    Claim text is shown to agents and humans in terminals and hook context;
    an escape sequence or a right-to-left override can hide or rewrite what
    they see. Newlines and tabs survive; everything else invisible does not.
    """
    if not text:
        return text or ""
    return _CONTROL.sub("", _ANSI.sub("", text))


def reject_secrets(text: str | None) -> None:
    if not text:
        return
    # Already-redacted values are markers, not secrets.
    masked = text.replace(SECRET_MARK, " ").replace(CRED_MARK, " ")
    for pat in _PUBLIC_DEFAULTS:
        masked = pat.sub(" ", masked)
    for pat in _SECRET_PATTERNS:
        if pat.search(masked):
            raise SecretError("claim contains a secret-shaped token; refuse to store")
