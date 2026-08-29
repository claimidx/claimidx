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
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I),
]


class SecretError(ValueError):
    pass


def reject_secrets(text: str | None) -> None:
    if not text:
        return
    for pat in _SECRET_PATTERNS:
        if pat.search(text):
            raise SecretError("claim contains a secret-shaped token; refuse to store")
