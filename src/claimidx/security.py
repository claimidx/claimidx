from __future__ import annotations

import re

_SECRET_PATTERNS = [
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
    masked = text
    for pat in _PUBLIC_DEFAULTS:
        masked = pat.sub(" ", masked)
    for pat in _SECRET_PATTERNS:
        if pat.search(masked):
            raise SecretError("claim contains a secret-shaped token; refuse to store")
