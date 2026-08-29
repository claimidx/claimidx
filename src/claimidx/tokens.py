"""Home-operator write tokens. Env CLAIMIDX_HOME_TOKEN or ~/.claimidx/tokens.json."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .config import config_path


def tokens_path() -> Path:
    return config_path().parent / "tokens.json"


def _load() -> dict:
    path = tokens_path()
    if not path.exists():
        return {"tokens": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"tokens": []}
    if not isinstance(data, dict):
        return {"tokens": []}
    data.setdefault("tokens", [])
    return data


def _save(data: dict) -> None:
    path = tokens_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def mint(name: str) -> str:
    token = "spt_" + secrets.token_urlsafe(24)
    data = _load()
    data["tokens"] = [t for t in data["tokens"] if t.get("name") != name]
    data["tokens"].append({"name": name, "token": token})
    _save(data)
    return token


def valid(presented: str) -> bool:
    presented = (presented or "").strip()
    if not presented:
        return False
    env = (os.environ.get("CLAIMIDX_HOME_TOKEN") or "").strip()
    if env and secrets.compare_digest(presented, env):
        return True
    for row in _load().get("tokens") or []:
        stored = (row.get("token") or "").strip()
        if stored and secrets.compare_digest(presented, stored):
            return True
    return False


def write_protection_enabled() -> bool:
    env = (os.environ.get("CLAIMIDX_HOME_TOKEN") or "").strip()
    return bool(env or (_load().get("tokens") or []))
