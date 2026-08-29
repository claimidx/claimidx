"""User config at ~/.claimidx/config.json. Env vars always win.

CLAIMIDX_* is canonical. SPOOR_* is still read so existing homes keep working.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ENV = {
    "owner": ("CLAIMIDX_OWNER", "SPOOR_OWNER"),
    "agent": ("CLAIMIDX_AGENT", "SPOOR_AGENT"),
    "home": ("CLAIMIDX_HOME", "SPOOR_HOME"),
    "home_api": ("CLAIMIDX_HOME_API", "SPOOR_HOME_API"),
    "home_token": ("CLAIMIDX_HOME_TOKEN", "SPOOR_HOME_TOKEN"),
    "org": ("CLAIMIDX_ORG", "SPOOR_ORG"),
    "share": ("CLAIMIDX_SHARE", "SPOOR_SHARE"),
}


def config_path() -> Path:
    override = os.environ.get("CLAIMIDX_CONFIG") or os.environ.get("SPOOR_CONFIG")
    if override:
        return Path(override)
    modern = Path.home() / ".claimidx" / "config.json"
    legacy = Path.home() / ".spoor" / "config.json"
    if modern.exists() or not legacy.exists():
        return modern
    return legacy


def load() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict[str, Any]) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = load()
    current.update({k: v for k, v in data.items() if v is not None})
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return path


def get(key: str, default: Any = "") -> Any:
    names = ENV.get(key, ())
    for env_name in names:
        raw = os.environ.get(env_name)
        if raw is not None and raw != "":
            if key == "share":
                return raw.strip().lower() not in ("0", "false", "no", "off")
            return raw
    data = load()
    if key in data:
        return data[key]
    return default
