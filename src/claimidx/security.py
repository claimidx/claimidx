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


def reject_secrets(text: str | None) -> None:
    if not text:
        return
    masked = text
    for pat in _PUBLIC_DEFAULTS:
        masked = pat.sub(" ", masked)
    for pat in _SECRET_PATTERNS:
        if pat.search(masked):
            raise SecretError("claim contains a secret-shaped token; refuse to store")
