"""Identity. Any agent, any provider, any runtime.

An optional ignored local roster may label a home. The public package carries
no people, organizations, roles, or deployment-specific identity data.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

ROSTER: dict[str, dict[str, str]] = {}

PACKAGED = Path(__file__).resolve().parents[2] / "team" / "roster.json"

_SLUG = re.compile(r"[^a-z0-9._-]+")


def agent_slug(name: str) -> str:
    """Turn any agent/provider label into a DID-safe slug."""
    s = (name or "").strip().lower().replace(" ", "-").replace("/", "-")
    s = _SLUG.sub("-", s).strip("-.")
    return s or "agent"


def did_for_agent(name: str) -> str:
    slug = agent_slug(name)
    rec = ROSTER.get(slug)
    if rec:
        return rec["did"]
    return f"did:claimidx:{slug}"


def resolve_owner(explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip()
    env = (os.environ.get("CLAIMIDX_OWNER") or "").strip()
    if env:
        return env
    name = (os.environ.get("CLAIMIDX_AGENT") or "").strip()
    if name:
        return did_for_agent(name)
    try:
        from .config import get as cfg_get

        cfg_own = (cfg_get("owner") or "").strip()
        if cfg_own:
            return cfg_own
        cfg_agent = (cfg_get("agent") or "").strip()
        if cfg_agent:
            return did_for_agent(cfg_agent)
    except Exception:
        pass
    provisioned = auto_identity()
    if provisioned:
        return provisioned["owner"]
    return "did:claimidx:anon"


def default_agent_name() -> str:
    """`agent-<6 hex>`: unique enough, and carries no username or hostname into a public ledger."""
    import secrets

    return f"agent-{secrets.token_hex(3)}"


def auto_identity(*, quiet: bool = False) -> dict | None:
    """Provision an owner DID (and an Ed25519 key) the first time nothing is configured.

    Invisible until it matters: only runs when no --own, env, or config names an
    owner. CLAIMIDX_AUTO_IDENTITY=0 disables it and anonymous writes stay refused.
    Returns the saved record, or None when disabled or when saving is impossible.
    """
    if (os.environ.get("CLAIMIDX_AUTO_IDENTITY") or "1").strip() in {"0", "false", "no"}:
        return None
    try:
        from . import config

        agent = default_agent_name()
        own = did_for_agent(agent)
        data: dict = {"owner": own, "agent": agent, "share": True}
        key_path = config.config_path().parent / "identity.json"
        if not key_path.exists():
            from .identity import generate_identity

            data["key"] = generate_identity(key_path)["did"]
        config.save(data)
        os.environ["CLAIMIDX_OWNER"] = own
        os.environ["CLAIMIDX_AGENT"] = agent
    except Exception:
        return None
    if not quiet:
        import sys

        print(f"claimidx: identity created {own} (claimidx init --agent <name> to rename)", file=sys.stderr)
    return data


def whoami(explicit: str | None = None) -> dict:
    did = resolve_owner(explicit)
    listed = next((k for k, v in ROSTER.items() if v["did"] == did), None)
    rec = ROSTER.get(listed or "", {})
    agent = listed or agent_slug(did.split(":")[-1] if ":" in did else did)
    valid = bool(did) and did.startswith("did:") and did not in ("did:claimidx:anon", "anon")
    key = ""
    try:
        from .config import get as cfg_get

        key = str(cfg_get("key") or "")
    except Exception:
        pass
    return {
        "did": did,
        "agent": agent,
        "key": key,
        "role": rec.get("role") or "agent",
        "listed": listed is not None,
        "wired": valid,
        "env_owner": os.environ.get("CLAIMIDX_OWNER") or "",
        "env_agent": os.environ.get("CLAIMIDX_AGENT") or "",
    }


def load_roster() -> list[dict]:
    if PACKAGED.exists():
        data = json.loads(PACKAGED.read_text())
        return data.get("agents", [])
    return [{"agent": k, **v} for k, v in ROSTER.items()]


def activity(store) -> list[dict]:
    by = store.event_activity() if hasattr(store, "event_activity") else {}
    if not by:
        # legacy store: last-N window hid other providers under one operator
        events = store.events(limit=10000)
        for ev in events:
            actor = ev.get("actor") or "did:claimidx:anon"
            slot = by.setdefault(
                actor,
                {"did": actor, "publish": 0, "confirm": 0, "fail": 0, "ask": 0, "share": 0, "last": ev.get("ts")},
            )
            kind = ev.get("kind") or ""
            if kind in ("home-push", "home-propose", "share"):
                slot["share"] += 1
            elif kind in slot:
                slot[kind] += 1
            elif kind == "hook":
                slot["ask"] += 1
            ts = ev.get("ts")
            if ts and (not slot.get("last") or str(ts) > str(slot["last"])):
                slot["last"] = ts
    out = []
    seen: set[str] = set()
    for name, rec in ROSTER.items():
        row = by.pop(rec["did"], {"did": rec["did"], "publish": 0, "confirm": 0, "fail": 0, "ask": 0, "share": 0, "last": None})
        row["agent"] = name
        row["role"] = rec["role"]
        row["listed"] = True
        row["wired"] = True
        out.append(row)
        seen.add(rec["did"])
    for did, row in by.items():
        if did in seen:
            continue
        row["agent"] = agent_slug(did.split(":")[-1] if ":" in did else did)
        row["role"] = "agent"
        row["listed"] = False
        row["wired"] = bool(did) and did.startswith("did:") and did not in ("did:claimidx:anon", "anon")
        out.append(row)
    out.sort(key=lambda r: (str(r.get("last") or ""), int(r.get("ask") or 0) + int(r.get("publish") or 0)), reverse=True)
    return out
