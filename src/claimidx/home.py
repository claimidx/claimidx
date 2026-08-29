"""Federation with the public home ledger and an optional live home API.

Public commons (every agent can read):
    CLAIMIDX_HOME  — URL of a claims.jsonl ledger
                  default: GitHub raw data/claims.jsonl on main

Live home (writes):
    CLAIMIDX_HOME_API   — base URL of a `claimidx serve` you control
    CLAIMIDX_HOME_TOKEN — optional bearer token the home operator issued

Agents never write the GitHub file directly. They:
    1. publish locally under a DID
    2. `home-push` to a live API, or
    3. `home-propose` a jsonl line and open a PR against data/claims.jsonl

Anything pulled from home is tagged src=home and quarantined.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .match import rank
from .models import Claim
from .policy import PolicyError
from .security import SecretError

DEFAULT_LEDGER = (
    "https://raw.githubusercontent.com/claimidx/claimidx/main/data/claims.jsonl"
)
USER_AGENT = "claimidx-home/0.4.0"


class HomeError(RuntimeError):
    pass


def ledger_url() -> str:
    env = (os.environ.get("CLAIMIDX_HOME") or "").strip()
    if env:
        return env
    try:
        from .config import get as cfg_get

        cfg = str(cfg_get("home") or "").strip()
        if cfg:
            return cfg
    except Exception:
        pass
    return DEFAULT_LEDGER


def api_url() -> str:
    env = (os.environ.get("CLAIMIDX_HOME_API") or "").rstrip("/")
    if env:
        return env
    try:
        from .config import get as cfg_get

        return str(cfg_get("home_api") or "").rstrip("/")
    except Exception:
        return ""


def api_token() -> str:
    env = (os.environ.get("CLAIMIDX_HOME_TOKEN") or "").strip()
    if env:
        return env
    try:
        from .config import get as cfg_get

        return str(cfg_get("home_token") or "").strip()
    except Exception:
        return ""


def share_enabled() -> bool:
    try:
        from .config import get as cfg_get

        return bool(cfg_get("share", True))
    except Exception:
        return True


def outbox_path() -> Path:
    override = os.environ.get("CLAIMIDX_OUTBOX")
    if override:
        return Path(override)
    from .config import config_path

    return config_path().parent / "outbox.jsonl"


def _get(url: str, timeout: float = 20.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/plain, application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HomeError(f"home GET {e.code} {url}") from e
    except urllib.error.URLError as e:
        raise HomeError(f"home unreachable: {e.reason}") from e


def _post(url: str, payload: dict[str, Any], token: str = "", timeout: float = 20.0) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise HomeError(f"home POST {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise HomeError(f"home unreachable: {e.reason}") from e
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError as e:
        raise HomeError(f"home returned non-json: {e}") from e


def parse_ledger(text: str) -> tuple[list[Claim], list[str]]:
    """Parse jsonl. Returns (accepted, skip_reasons). Does not persist."""
    accepted: list[Claim] = []
    skipped: list[str] = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
            raw["src"] = "home"
            claim = Claim.model_validate(raw)
            accepted.append(claim)
        except (json.JSONDecodeError, PolicyError, SecretError, ValueError) as e:
            skipped.append(f"L{i}: {type(e).__name__}: {e}")
    return accepted, skipped


def _read_target(target: str) -> str:
    """HTTP URL, file: URL, or a local jsonl path."""
    raw = (target or "").strip()
    if not raw:
        raise HomeError("empty home URL")
    if raw.startswith("file:"):
        from urllib.parse import unquote, urlparse
        from urllib.request import url2pathname

        parsed = urlparse(raw)
        path = url2pathname(unquote(parsed.path))
        p = Path(path)
        if not p.is_file():
            raise HomeError(f"ledger file not found: {raw}")
        return p.read_text(encoding="utf-8")
    if not raw.startswith(("http://", "https://")):
        p = Path(raw)
        if p.is_file():
            return p.read_text(encoding="utf-8")
        raise HomeError(f"ledger file not found: {raw}")
    return _get(raw).decode("utf-8", errors="replace")


def fetch_ledger(url: str | None = None) -> tuple[list[Claim], list[str], str]:
    target = url or ledger_url()
    text = _read_target(target)
    claims, skipped = parse_ledger(text)
    return claims, skipped, target


def pull(store, url: str | None = None) -> dict[str, Any]:
    """Fetch the public ledger and ingest under quarantine."""
    claims, skipped, target = fetch_ledger(url)
    imported = 0
    existed = 0
    refused = 0
    for c in claims:
        if store.get(c.id):
            existed += 1
            continue
        try:
            c.src = "home"
            store.put(c)
            imported += 1
        except (PolicyError, SecretError, ValueError):
            refused += 1
    store.log("home-pull", os.environ.get("CLAIMIDX_OWNER") or "did:claimidx:anon")
    return {
        "url": target,
        "seen": len(claims),
        "imported": imported,
        "existed": existed,
        "refused": refused,
        "skipped": skipped[:20],
        "skipped_n": len(skipped),
    }


def ask_home(query: dict, k: int = 5, url: str | None = None) -> dict[str, Any]:
    """Rank against the live ledger without writing local state."""
    claims, skipped, target = fetch_ledger(url)
    hits = rank(query, claims, k=k)
    return {
        "url": target,
        "hit": bool(hits),
        "n": len(hits),
        "pool": len(claims),
        "skipped_n": len(skipped),
        "claims": [
            {
                "sim": round(s, 4),
                "score": round(c.score(), 4),
                "id": c.id,
                "st": c.st,
                "cls": c.cls,
                "err": c.err,
                "fix": c.fix.model_dump(),
                "eval": c.eval.model_dump(),
                "own": c.own,
                "src": "home",
            }
            for c, s in hits
        ],
    }


def propose_line(claim: Claim) -> str:
    """One jsonl line suitable for a PR against data/claims.jsonl.

    Always a public projection: same fingerprint, no notes/paths/project evals.
    """
    from .public import project_public

    payload = json.loads(project_public(claim).model_dump_json())
    payload["src"] = "home"
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def publish_home(claim: Claim, api: str | None = None, token: str | None = None, force: bool = False) -> dict[str, Any]:
    """POST a local claim to a live home API. Never hits GitHub directly."""
    base = (api or api_url()).rstrip("/")
    if not base:
        raise HomeError(
            "no CLAIMIDX_HOME_API; print a PR line with home-propose or point "
            "CLAIMIDX_HOME_API at a `claimidx serve` you control"
        )
    body = {
        "id": claim.id,
        "err": claim.err,
        "fix_k": claim.fix.k,
        "fix_b": claim.fix.b,
        "eval": claim.eval.cmd,
        "expect": claim.eval.expect,
        "cls": claim.cls,
        "eco": claim.eco,
        "rt": claim.rt,
        "dep": claim.dep,
        "tool": claim.tool,
        "tried": claim.tried,
        "own": claim.own,
        "model": claim.model,
        "note": claim.note,
        "force": force,
    }
    return _post(f"{base}/api/publish", body, token=token if token is not None else api_token())


def already_shared(store, claim_id: str) -> bool:
    if hasattr(store, "has_event"):
        return store.has_event(claim_id, ("home-push", "home-propose", "share"))
    for ev in store.events(limit=1000):
        if ev.get("claim_id") == claim_id and ev.get("kind") in ("home-push", "home-propose", "share"):
            return True
    return False


def share_claim(store, claim: Claim, *, api: str | None = None, token: str | None = None, force: bool = False) -> dict[str, Any]:
    """Push a local claim. Live private home gets the full record; the GitHub outbox gets a public projection."""
    if already_shared(store, claim.id) and not force:
        return {"status": "already", "id": claim.id}
    base = (api if api is not None else api_url()).rstrip("/")
    if base:
        result = publish_home(claim, api=base, token=token, force=force)
        store.log("home-push", claim.own, claim.id)
        return {"status": "pushed", "id": claim.id, "home": result}
    from .public import PublicSkip

    try:
        line = propose_line(claim)
    except PublicSkip as e:
        return {"status": "skipped", "id": claim.id, "reason": str(e)}
    path = outbox_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    store.log("home-propose", claim.own, claim.id)
    return {
        "status": "outbox",
        "id": claim.id,
        "path": str(path),
        "line": line,
        "hint": "no CLAIMIDX_HOME_API; queued a public projection for data/claims.jsonl",
    }


def share_pending(store, *, api: str | None = None, token: str | None = None, force: bool = False) -> dict[str, Any]:
    """Share every local (non-seed, non-home) claim that has not been submitted yet."""
    results: list[dict[str, Any]] = []
    skipped = 0
    for c in store.all():
        if getattr(c, "src", "local") in ("home", "seed"):
            skipped += 1
            continue
        if c.st == "rejected":
            skipped += 1
            continue
        if already_shared(store, c.id) and not force:
            skipped += 1
            continue
        results.append(share_claim(store, c, api=api, token=token, force=force))
    return {"n": len(results), "skipped": skipped, "results": results}


def maybe_share(store, claim: Claim) -> dict[str, Any] | None:
    """Auto-submit after ingest/confirm when a live home is configured."""
    if not share_enabled():
        return None
    if not api_url():
        return None
    try:
        return share_claim(store, claim)
    except HomeError as e:
        return {"status": "error", "id": claim.id, "error": str(e)}
