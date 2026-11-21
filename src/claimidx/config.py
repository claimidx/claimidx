"""User config at ~/.claimidx/config.json. Env vars always win."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ENV = {
    "owner": ("CLAIMIDX_OWNER",),
    "agent": ("CLAIMIDX_AGENT",),
    "home": ("CLAIMIDX_HOME",),
    "home_api": ("CLAIMIDX_HOME_API",),
    "home_token": ("CLAIMIDX_HOME_TOKEN",),
    "org": ("CLAIMIDX_ORG",),
    "share": ("CLAIMIDX_SHARE",),
}


def config_path() -> Path:
    override = os.environ.get("CLAIMIDX_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".claimidx" / "config.json"


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
    """Merge into config.json. The dir is 0700 and the file 0600 from creation (temp file + atomic replace)."""
    from .perms import write_private

    path = config_path()
    current = load()
    current.update({k: v for k, v in data.items() if v is not None})
    write_private(path, json.dumps(current, indent=2) + "\n")
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


def identity_path() -> Path:
    """Ed25519 key file, always a sibling of config.json (Windows-safe Path join)."""
    return config_path().parent / "identity.json"


def _text(value: Any) -> str:
    return str(value or "").strip()


def read_install_identity() -> dict[str, Any]:
    """Existing owner/agent/key from config.json, with identity.json as fallback.

    Does not create or rewrite any file. Config wins when both sources name an
    owner or agent and they disagree; ``warnings`` describes the mismatch.
    """
    cfg = load()
    cfg_owner = _text(cfg.get("owner"))
    cfg_agent = _text(cfg.get("agent"))
    ident_owner = ""
    ident_agent = ""
    ident_did = ""
    key_path = identity_path()
    ident_exists = key_path.exists()
    if ident_exists:
        try:
            payload = json.loads(key_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                ident_owner = _text(payload.get("owner"))
                ident_agent = _text(payload.get("agent"))
            from .identity import load_identity

            ident_did = _text(load_identity(key_path)[0])
        except (OSError, ValueError, json.JSONDecodeError, TypeError):
            ident_did = ident_did or ""
    warnings: list[str] = []
    if (cfg_owner or cfg_agent) and (ident_owner or ident_agent):
        agent_mismatch = bool(cfg_agent and ident_agent and cfg_agent != ident_agent)
        owner_mismatch = bool(cfg_owner and ident_owner and cfg_owner != ident_owner)
        if agent_mismatch or owner_mismatch:
            warnings.append(
                "# warning: config.json and identity.json disagree "
                f"(config agent={cfg_agent or '-'} owner={cfg_owner or '-'}, "
                f"identity agent={ident_agent or '-'} owner={ident_owner or '-'}); "
                "keeping config; identity.json is unchanged"
            )
    owner = cfg_owner or ident_owner
    agent = cfg_agent or ident_agent
    if not owner and ident_did and not (cfg_owner or cfg_agent or ident_owner or ident_agent):
        # Key only: keep the existing DID, do not mint a new agent-<hex> name.
        owner = ident_did
    return {
        "cfg_owner": cfg_owner,
        "cfg_agent": cfg_agent,
        "ident_owner": ident_owner,
        "ident_agent": ident_agent,
        "ident_did": ident_did,
        "ident_exists": ident_exists,
        "owner": owner,
        "agent": agent,
        "warnings": warnings,
        "has_config_name": bool(cfg_owner or cfg_agent),
        "has_ident_name": bool(ident_owner or ident_agent),
        "has_any": bool(cfg_owner or cfg_agent or ident_owner or ident_agent or ident_did or ident_exists),
    }


def resolve_init_names(
    *,
    agent_flag: str = "",
    own_flag: str = "",
    env_agent: str = "",
    env_owner: str = "",
) -> dict[str, Any]:
    """Pick owner/agent for ``claimidx init``.

    A bare init (no ``--agent`` / ``--own``) keeps names already stored in
    config.json, then identity.json. Auto-naming happens only when neither
    file exists (or both are empty of names and there is no key). An explicit
    ``--agent`` or ``--own`` may rename; the caller prints old → new.
    """
    from .team import agent_slug, default_agent_name, did_for_agent

    existing = read_install_identity()
    prev_owner = _text(existing.get("owner"))
    prev_agent = _text(existing.get("agent"))
    warnings = list(existing.get("warnings") or [])
    flag_agent = _text(agent_flag)
    flag_own = _text(own_flag)
    explicit = bool(flag_agent or flag_own)
    if explicit:
        if flag_own:
            own = flag_own
            agent = agent_slug(flag_agent) if flag_agent else (agent_slug(own.split(":")[-1]) if ":" in own else agent_slug(own))
        else:
            agent = agent_slug(flag_agent)
            own = did_for_agent(flag_agent)
        source = "flag"
        first = not existing["has_any"]
        renamed = (prev_agent and agent != prev_agent) or (prev_owner and own != prev_owner)
        return {
            "owner": own,
            "agent": agent,
            "source": source,
            "previous_owner": prev_owner,
            "previous_agent": prev_agent,
            "warnings": warnings,
            "renamed": renamed,
            "first_install": first,
        }

    if existing["has_config_name"] or existing["has_ident_name"] or existing["ident_did"] or existing["ident_exists"]:
        agent = prev_agent
        own = prev_owner or (did_for_agent(agent) if agent else "")
        source = "config" if existing["has_config_name"] else "identity"
        return {
            "owner": own,
            "agent": agent,
            "source": source,
            "previous_owner": prev_owner,
            "previous_agent": prev_agent,
            "warnings": warnings,
            "renamed": False,
            "first_install": False,
        }

    raw_agent = _text(env_agent)
    raw_own = _text(env_owner)
    if raw_own or raw_agent:
        agent = agent_slug(raw_agent) if raw_agent else (agent_slug(raw_own.split(":")[-1]) if raw_own and ":" in raw_own else "")
        own = raw_own or (did_for_agent(agent) if agent else "")
        return {
            "owner": own,
            "agent": agent,
            "source": "env",
            "previous_owner": "",
            "previous_agent": "",
            "warnings": warnings,
            "renamed": False,
            "first_install": True,
        }

    agent = default_agent_name()
    own = did_for_agent(agent)
    return {
        "owner": own,
        "agent": agent,
        "source": "auto",
        "previous_owner": "",
        "previous_agent": "",
        "warnings": warnings,
        "renamed": False,
        "first_install": True,
    }
