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

from . import __version__
from .fingerprint import fingerprint
from .match import hit_compact, rank
from .models import Claim
from .policy import PolicyError
from .security import SecretError

DEFAULT_LEDGER = "https://raw.githubusercontent.com/claimidx/claimidx/main/data/claims.jsonl"
# The commons: a public home every install shares to and pulls from unless told not to.
# No token, no PR; the repo's data/claims.jsonl is a snapshot of it for the offline fallback.
COMMONS_API = "https://home.claimidx.com/t/commons"
COMMONS_LEDGER = COMMONS_API + "/api/claims.jsonl"
USER_AGENT = f"claimidx-home/{__version__}"


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
    return COMMONS_LEDGER if commons_enabled() else DEFAULT_LEDGER


def commons_api() -> str:
    env = (os.environ.get("CLAIMIDX_COMMONS_API") or "").rstrip("/")
    if env:
        return env
    try:
        from .config import get as cfg_get

        return str(cfg_get("commons_api") or COMMONS_API).rstrip("/")
    except Exception:
        return COMMONS_API


def commons_enabled() -> bool:
    """On by default. Off with CLAIMIDX_COMMONS=0 or config `commons: false`."""
    raw = os.environ.get("CLAIMIDX_COMMONS")
    if raw is not None and raw.strip():
        return raw.strip().lower() not in ("0", "false", "no", "off")
    try:
        from .config import get as cfg_get

        val = cfg_get("commons", True)
    except Exception:
        return True
    if isinstance(val, str):
        return val.strip().lower() not in ("0", "false", "no", "off")
    return bool(val)


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
            got = fingerprint(err=claim.err, cls=claim.cls, eco=claim.eco, rt=claim.rt, dep=claim.dep)
            if claim.fp != got:
                skipped.append(f"L{i}: fp mismatch")
                continue
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
    try:
        text = _read_target(target)
    except HomeError:
        if url or target != COMMONS_LEDGER:
            raise
        target = DEFAULT_LEDGER  # the commons is unreachable: the repo snapshot is the offline copy
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
        "claims": [{**hit_compact(query, c, s), "own": c.own, "src": "home"} for c, s in hits],
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
        raise HomeError("no CLAIMIDX_HOME_API; print a PR line with home-propose or point CLAIMIDX_HOME_API at a `claimidx serve` you control")
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


def commons_shared(store, claim_id: str) -> bool:
    if hasattr(store, "has_event"):
        return store.has_event(claim_id, ("commons-push",))
    return any(ev.get("claim_id") == claim_id and ev.get("kind") == "commons-push" for ev in store.events(limit=1000))


def _public_payload(claim: Claim) -> dict[str, Any]:
    payload = json.loads(propose_line(claim))
    payload["fix_k"] = (payload.get("fix") or {}).get("k")
    payload["fix_b"] = (payload.get("fix") or {}).get("b")
    return payload


def push_commons(store, claim: Claim, *, force: bool = False) -> dict[str, Any]:
    """Push the public projection to the commons; queue it in the outbox when the commons is unreachable."""
    from .public import HINT_WARN, PublicSkip, eval_is_proof

    if not force and commons_shared(store, claim.id):
        return {"status": "already", "id": claim.id}
    if not force and not eval_is_proof(claim.eval.cmd):
        return {"status": "skipped", "id": claim.id, "reason": HINT_WARN}
    try:
        payload = _public_payload(claim)
    except PublicSkip as e:
        return {"status": "skipped", "id": claim.id, "reason": str(e)}
    if force:
        payload["force"] = True
    try:
        result = _post(commons_api() + "/api/publish", payload)
    except HomeError as e:
        path = outbox_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n")
        store.log("home-propose", claim.own, claim.id, {"commons": "outbox", "error": str(e)[:200]})
        return {"status": "outbox", "id": claim.id, "path": str(path), "hint": f"commons unreachable ({str(e)[:80]}); queued, `claimidx sync` sends it"}
    store.log("commons-push", claim.own, claim.id, {"exists": bool(result.get("exists")) if isinstance(result, dict) else False})
    return {"status": "commons", "id": claim.id, "commons": result}


def flush_outbox(store) -> dict[str, Any]:
    """Send queued public rows to the commons; keep the ones that still fail."""
    path = outbox_path()
    if not path.exists() or not commons_enabled():
        return {"sent": 0, "kept": 0}
    kept: list[str] = []
    sent = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError:
            continue
        try:
            _post(commons_api() + "/api/publish", payload)
        except HomeError:
            kept.append(line)
            continue
        sent += 1
        cid = str(payload.get("id") or "")
        if cid:
            store.log("commons-push", str(payload.get("own") or "did:claimidx:anon"), cid, {"from": "outbox"})
    if kept:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return {"sent": sent, "kept": len(kept)}


def already_shared(store, claim_id: str) -> bool:
    if hasattr(store, "has_event"):
        return store.has_event(claim_id, ("home-push", "home-propose", "share"))
    for ev in store.events(limit=1000):
        if ev.get("claim_id") == claim_id and ev.get("kind") in ("home-push", "home-propose", "share"):
            return True
    return False


def share_claim(store, claim: Claim, *, api: str | None = None, token: str | None = None, force: bool = False) -> dict[str, Any]:
    """Push a local claim: the full record to a private home when one is configured, and the public
    projection to the commons unless it is switched off. With neither, the projection is queued.
    """
    base = (api if api is not None else api_url()).rstrip("/")
    out: dict[str, Any] = {"status": "already", "id": claim.id}
    if base and (force or not already_shared(store, claim.id)):
        result = publish_home(claim, api=base, token=token, force=force)
        store.log("home-push", claim.own, claim.id)
        out.update({"status": "pushed", "home": result})
    if commons_enabled():
        commons = push_commons(store, claim, force=force)
        out["commons"] = commons
        if commons.get("status") in {"commons", "outbox"} and out["status"] == "already":
            out["status"] = commons["status"]
        if commons.get("status") == "outbox":
            out["path"] = commons.get("path")
            out["hint"] = commons.get("hint")
        return out
    if base:
        return out
    if already_shared(store, claim.id) and not force:
        return out
    from .public import HINT_WARN, PublicSkip, eval_is_proof

    if not force and not eval_is_proof(claim.eval.cmd):
        # The public ledger is prior art other agents replay. A `true` eval
        # cannot be replayed, so it stays local until it carries a recipe.
        return {"status": "skipped", "id": claim.id, "reason": HINT_WARN + " (or share --force)"}
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
    flushed = flush_outbox(store)
    for c in store.all():
        if getattr(c, "src", "local") in ("home", "seed"):
            skipped += 1
            continue
        if c.st == "rejected":
            skipped += 1
            continue
        done_private = already_shared(store, c.id) or not (api if api is not None else api_url())
        done_commons = commons_shared(store, c.id) or not commons_enabled()
        if done_private and done_commons and not force:
            skipped += 1
            continue
        r = share_claim(store, c, api=api, token=token, force=force)
        if r.get("status") == "already":
            skipped += 1
            continue
        results.append(r)
    return {"n": len(results), "skipped": skipped, "outbox": flushed, "results": results}


def maybe_share(store, claim: Claim) -> dict[str, Any] | None:
    """Auto-submit after ingest/confirm when a live home is configured."""
    if not share_enabled():
        return None
    if not api_url() and not commons_enabled():
        return None
    try:
        return share_claim(store, claim)
    except HomeError as e:
        return {"status": "error", "id": claim.id, "error": str(e)}


def share_observation(store, claim: Claim, *, held: bool, actor: str, replayed: bool = True) -> dict[str, Any] | None:
    """Report a replayed hold or miss on a claim the home already has, so its counters reflect other agents.

    `share_claim` re-publishes a row; that is a no-op once the home has it.
    The home's confirm/fail endpoints are what move nc/nf/nr there, and they
    are what `impact` reads back. Live home only; the public jsonl is a
    projection that only PR lines can change. Never raises.
    """
    if not share_enabled() or not replayed:
        return None
    base = api_url()
    token = api_token()
    if not base and commons_enabled() and commons_shared(store, claim.id):
        base, token = commons_api(), ""  # the commons counts replays too; that is how a claim earns its standing
    if not base:
        return None
    verb = "confirm" if held else "fail"
    from urllib.parse import quote

    url = f"{base}/api/claims/{quote(claim.id)}/{verb}?own={quote(actor)}" + ("&replay=true" if held else "")
    try:
        result = _post(url, {}, token=token)
    except HomeError as e:
        return {"status": "error", "id": claim.id, "error": str(e)}
    store.log("home-" + verb, actor, claim.id, {"replayed": True})
    row = result.get("claim", result) if isinstance(result, dict) else {}
    return {"status": verb, "id": claim.id, "home": {k: row.get(k) for k in ("nc", "nf", "nr", "st") if isinstance(row, dict) and k in row}}
