"""`claimidx impact`: the number that keeps the hook installed.

Agents do not have egos, but the human deciding whether Claimidx stays in
the config does. This reads the local event log (never the raw errors) and,
when reachable, the public ledger, and answers: how many retries did the
index save this week, what did I contribute, and did anyone else use it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from .store import Store


def _ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _rows(store: Store, since: datetime) -> list[dict]:
    out: list[dict] = []
    for r in store.all_events():
        when = _ts(r["ts"])
        if when is None or when < since:
            continue
        detail: dict = {}
        raw = r.get("detail")
        if raw:
            try:
                parsed = json.loads(raw)
                detail = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                detail = {}
        out.append({"claim_id": r["claim_id"] or "", "kind": r["kind"] or "", "actor": r["actor"] or "", "ts": when, "detail": detail})
    return out


def local_impact(store: Store, *, days: int = 7, own: str = "") -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = _rows(store, since)
    asks = [r for r in rows if r["kind"] in ("ask", "hook")]
    hits = [r for r in asks if r["detail"].get("hit") is True]
    misses = [r for r in asks if r["detail"].get("hit") is False]

    def _after(kinds: tuple[str, ...]) -> dict[str, datetime]:
        latest: dict[str, datetime] = {}
        for r in rows:
            if r["kind"] in kinds and r["claim_id"]:
                latest[r["claim_id"]] = max(latest.get(r["claim_id"], r["ts"]), r["ts"])
        return latest

    confirmed_at = _after(("confirm", "confirm-replay"))
    failed_at = _after(("fail",))
    # A retry skipped: the ask hit, and that hit was then confirmed by you, after the ask.
    skipped = {r["claim_id"] for r in hits if r["claim_id"] and confirmed_at.get(r["claim_id"], since) > r["ts"]}
    misled = {r["claim_id"] for r in hits if r["claim_id"] and failed_at.get(r["claim_id"], since) > r["ts"] and r["claim_id"] not in skipped}
    published = [r for r in rows if r["kind"] == "publish" and (not own or r["actor"] == own)]
    miss_fps = {r["detail"].get("fp") for r in misses if r["detail"].get("fp")}
    published_fps = set()
    for r in published:
        c = store.get(r["claim_id"])
        if c:
            published_fps.add(c.fp)
    solved = miss_fps & published_fps
    held = [r for r in rows if r["kind"] == "confirm-replay"]
    ms = sum(int(r["detail"].get("ms") or 0) for r in asks if isinstance(r["detail"].get("ms"), int))
    return {
        "days": days,
        "asks": len(asks),
        "hits": len(hits),
        "misses": len(misses),
        "retries_skipped": len(skipped),
        "hits_that_failed": len(misled),
        "claims_published": len(published),
        "misses_you_solved": len(solved),
        "replays_held": len(held),
        "ask_ms": ms,
    }


def ledger_impact(own: str, *, url: str | None = None, store: Store | None = None) -> dict[str, Any]:
    """Your claims as the ledger sees them, minus your own observations. Counters there are hearsay, not local proof."""
    from .home import fetch_ledger

    claims, _skipped, target = fetch_ledger(url)
    mine = [c for c in claims if c.own == own]

    def others(c, field: str) -> int:
        remote = int(getattr(c, field, 0) or 0)
        local = store.get(c.id) if store is not None else None
        return max(0, remote - int(getattr(local, field, 0) or 0)) if local is not None else remote

    return {
        "ledger": target,
        "your_claims": len(mine),
        "confirmed_by_others": sum(others(c, "nc") for c in mine),
        "replayed_by_others": sum(others(c, "nr") for c in mine),
        "failed_by_others": sum(others(c, "nf") for c in mine),
        "ids": [c.id for c in mine[:10]],
    }


def commons_impact(own: str, *, days: int = 30) -> dict[str, Any]:
    """Your standing on the commons: holds by other agents, distinct verifiers, rank. Signed holds only."""
    from .board import fetch_leaderboard

    board = fetch_leaderboard(days=days, limit=200, own=own)
    you = board.get("you") or {}
    author = you.get("author") or {}
    verifier = you.get("verifier") or {}
    return {
        "days": board.get("days", days),
        "held_by_others": int(author.get("holds") or 0),
        "standing": author.get("standing", 0),
        "pending": int(author.get("pending") or 0),
        "verifiers": int(author.get("verifiers") or 0),
        "rank": author.get("rank"),
        "you_held": int(verifier.get("holds") or 0),
        "verifier_rank": verifier.get("rank"),
        "authors_on_board": len(board.get("authors") or []),
    }


_FUNNEL_KINDS = {
    "commons-push": "push",
    "commons-refused": "refused",
    "commons-skip": "skip",
    "commons-hold": "hold",
    "commons-fail": "fail",
}


def commons_funnel(store: Store, *, days: int = 30) -> dict[str, Any]:
    """Local commons publish funnel from the event log (no network)."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = _rows(store, since)
    pushes = [r for r in rows if r["kind"] == "commons-push"]
    refused = [r for r in rows if r["kind"] == "commons-refused"]
    skipped = [r for r in rows if r["kind"] == "commons-skip"]
    holds = [r for r in rows if r["kind"] == "commons-hold"]
    fails = [r for r in rows if r["kind"] == "commons-fail"]

    by: dict[str, dict[str, int]] = {}
    for r in rows:
        key = _FUNNEL_KINDS.get(r["kind"])
        actor = r["actor"]
        if not key or not actor:
            continue
        bucket = by.setdefault(actor, {"push": 0, "refused": 0, "skip": 0, "hold": 0, "fail": 0})
        bucket[key] += 1
    by_actor_rows: list[dict[str, Any]] = [{"actor": a, **counts} for a, counts in by.items()]

    def _funnel_total(row: dict[str, Any]) -> int:
        return int(row["push"]) + int(row["refused"]) + int(row["skip"]) + int(row["hold"]) + int(row["fail"])

    by_actor = sorted(by_actor_rows, key=lambda row: -_funnel_total(row))

    # First-ever commons-push per actor across full history; count those whose debut is in-window.
    first_at: dict[str, datetime] = {}
    for r in _rows(store, datetime(1970, 1, 1, tzinfo=UTC)):
        if r["kind"] != "commons-push" or not r["actor"] or r["actor"] in first_at:
            continue
        first_at[r["actor"]] = r["ts"]
    first_share = sorted((a for a, t in first_at.items() if t >= since), key=lambda a: first_at[a])
    return {
        "days": days,
        "pushes": {"count": len(pushes), "actors": len({r["actor"] for r in pushes if r["actor"]})},
        "refused": len(refused),
        "skipped": len(skipped),
        "holds": len(holds),
        "fails": len(fails),
        "by_actor": by_actor,
        "first_share_actors": {"count": len(first_share), "actors": first_share[:20]},
    }


_LIFECYCLE_KINDS = {
    "install": "install",
    "init": "init",
    "ask": "ask",
    "hook": "ask",
    "sync": "sync",
    "home-pull": "sync",
    "confirm": "confirm",
    "confirm-replay": "confirm",
    "publish": "publish",
    "commons-push": "share",
    "share-explicit": "share",
    "home-push": "share",
}

_LIFECYCLE_ORDER = ("install", "init", "ask", "sync", "confirm", "publish", "share")

_FUNNEL_EXCLUDED = frozenset({"did:claimidx:seed", "did:claimidx:anon", "anon", ""})

# Commons-derived proxy (production ledger). Not install->init drop-off.
# Prefix patterns end in * (same shape as commons_excluded on the home worker).
# Personal operator DIDs stay in CLAIMIDX_OPERATOR_DID / CLAIMIDX_REWARDS_EXCLUDE / config —
# do not hardcode people. Role/test prefixes below drop COO/Implementation/Social falsifiers
# that use named agents (e.g. CLAIMIDX_AGENT=impl-falsifier-…). Auto-minted agent-<hex>
# stranger DIDs stay countable. Known leaked Path B falsifiers (bare agent-<hex> that
# prefixes cannot catch) are listed exactly below.
_PROXY_DEFAULT_EXCLUDE = frozenset(
    {
        "did:claimidx:seed",
        "did:claimidx:anon",
        "anon",
        "",
        "did:claimidx:grok",
        "did:claimidx:codex",
        "did:claimidx:claude*",
        # Durable role/test prefixes (honest stranger scoreboard).
        "did:claimidx:coo-*",
        "did:claimidx:impl-*",
        "did:claimidx:implementation-*",
        "did:claimidx:social-*",
        "did:claimidx:falsifier-*",
        "did:claimidx:test-*",
        "did:claimidx:devbot-*",
        "did:claimidx:ops-*",
        "did:claimidx:ci-*",
        # Leaked Path B falsifier (0.7.11 test); prefixes miss bare agent-<hex>.
        "did:claimidx:agent-5765cb",
    }
)
COUNTABLE_GOAL = 100


def _operator_did_patterns() -> list[str]:
    """CLAIMIDX_OPERATOR_DID (comma-separated exact DID or prefix*): ops allowlist to drop."""
    import os

    env = os.environ.get("CLAIMIDX_OPERATOR_DID") or ""
    return [x.strip() for x in env.split(",") if x.strip()]


def _proxy_excluded(extra: list[str] | None = None) -> list[str]:
    """Exact DIDs and trailing-* prefixes: defaults + operator DID + rewards_exclude + extras."""
    from .rewards import excluded_owners

    out: list[str] = []
    seen: set[str] = set()
    for raw in (*_PROXY_DEFAULT_EXCLUDE, *_operator_did_patterns(), *sorted(excluded_owners(extra))):
        v = (raw or "").strip()
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def _proxy_is_excluded(did: str, patterns: list[str]) -> bool:
    d = (did or "").strip()
    if not d:
        return True
    for p in patterns:
        if not p:
            continue
        if p.endswith("*"):
            if d.startswith(p[:-1]):
                return True
        elif d == p:
            return True
    return False


def commons_owner_proxy(
    *,
    days: int = 30,
    exclude: list[str] | None = None,
    url: str | None = None,
    claims: list | None = None,
    ledger: str | None = None,
) -> dict[str, Any]:
    """Distinct non-operator owners on the public commons with published/held claims.

    Proxy only: the ledger has no install/init/sync stage events. Local-only DIDs that
    never shared are invisible. `countable` is all-time published owners after exclude;
    `recent_published` is owners with a claim.ts inside the lookback window.
    Excludes defaults + CLAIMIDX_OPERATOR_DID + CLAIMIDX_REWARDS_EXCLUDE + --exclude.
    """
    from .models import Claim

    patterns = _proxy_excluded(exclude)
    target = ledger or ""
    rows: list[Claim]
    if claims is not None:
        rows = list(claims)
    else:
        from .home import fetch_ledger

        rows, _skipped, target = fetch_ledger(url)

    since = datetime.now(UTC) - timedelta(days=max(1, min(int(days or 30), 365)))
    published: set[str] = set()
    held: set[str] = set()
    confirmed: set[str] = set()
    recent: set[str] = set()
    excluded_owners_seen: set[str] = set()
    for c in rows:
        own = (getattr(c, "own", None) or "").strip()
        if _proxy_is_excluded(own, patterns):
            if own:
                excluded_owners_seen.add(own)
            continue
        published.add(own)
        if int(getattr(c, "nr", 0) or 0) > 0:
            held.add(own)
        if (getattr(c, "st", None) or "") == "confirmed":
            confirmed.add(own)
        ts = getattr(c, "ts", None)
        if ts is None:
            when = None
        elif hasattr(ts, "isoformat"):
            when = _ts(ts.isoformat())
        else:
            when = _ts(str(ts))
        if when is not None and when >= since:
            recent.add(own)

    countable = sorted(published)
    held_ids = sorted(held)
    confirmed_ids = sorted(confirmed)
    recent_ids = sorted(recent)
    return {
        "kind": "commons_proxy",
        "ledger": target,
        "claims": len(rows),
        "days": max(1, min(int(days or 30), 365)),
        "exclude": patterns,
        "excluded_owners_seen": len(excluded_owners_seen),
        "countable": len(countable),
        "countable_goal": COUNTABLE_GOAL,
        "countable_ids": countable[:20],
        "held": len(held_ids),
        "held_ids": held_ids[:20],
        "confirmed": len(confirmed_ids),
        "confirmed_ids": confirmed_ids[:20],
        "recent_published": len(recent_ids),
        "recent_published_ids": recent_ids[:20],
        "limits": (
            "commons-derived proxy only: distinct non-operator owners with a published claim on the public ledger; "
            "held = nr>0 on at least one owned claim; not install->init drop-off; local-only DIDs never shared are invisible"
        ),
    }


# Live py@3.13 first-hold on the commons (audioop-lts). Do not default spr_a11c… (py@3.12).
FIRST_HOLD_ID = "cix_bdc82291f2fbb06a"
FIRST_HOLD_RT = "py@3.13"
# Real outward share only — local `publish` is claim-without-share (publish_no_share).
_SHARE_KINDS = frozenset({"commons-push", "share-explicit", "home-push"})
_HOLD_KINDS = frozenset({"confirm", "confirm-replay", "commons-hold"})
_PUBLISH_KINDS = frozenset({"publish"})


def path_b_cta(store: Store | None, actor: str | None = None) -> dict[str, Any]:
    """Path B conversion status for ask / home-ask / post-hold / claim envelopes.

    Ask may auto-mint a local DID; that is not a countable commons DID until a
    live share (commons-push / share-explicit / home-push). Local publish alone
    is publish_no_share — keep `next` pointed at `claimidx share`. When not
    countable, `next` is staged: first-hold → claim --yes (one-shot: online
    --yes continues into share; `claimidx share` is the offline/local safety net).
    """
    did = (actor or "").strip()
    minted = bool(did) and did.startswith("did:") and did not in _FUNNEL_EXCLUDED
    held = False
    published = False
    shared = False
    if minted and store is not None:
        for ev in store.events(limit=500, actor=did):
            kind = ev.get("kind") or ""
            if kind in _SHARE_KINDS:
                shared = True
            elif kind in _HOLD_KINDS:
                held = True
            elif kind in _PUBLISH_KINDS:
                published = True
            if shared and held and published:
                break
    countable = bool(minted and shared)
    out: dict[str, Any] = {
        "countable": countable,
        "minted": minted,
        "did": did if minted else "",
        "held": bool(minted and held),
        "published": bool(minted and published),
        "shared": bool(minted and shared),
    }
    if not countable:
        share_step = "claimidx share"
        # Path B one-shot: online claim --yes continues into share (no separate skippable step).
        claim_step = "claimidx claim --yes"
        if published and not shared:
            out["why"] = "publish_no_share: local claim is not a countable commons DID until share"
            out["next"] = share_step
        elif held and not published:
            out["why"] = "hold alone is not a countable commons DID — claim --yes (shares when online)"
            out["next"] = claim_step
        else:
            out["why"] = "ask alone does not create a countable commons DID"
            out["next"] = f"claimidx init --agent <you> && claimidx apply {FIRST_HOLD_ID} --cwd . --yes && {claim_step}"
        out["first_hold"] = {"id": FIRST_HOLD_ID, "rt": FIRST_HOLD_RT}
    return out


def harness_surfaces(harness: dict | None) -> list[str]:
    """Harness names that were written this run. Names only — never paths."""
    out: list[str] = []
    if not isinstance(harness, dict):
        return out
    for name, rec in harness.items():
        if name == "skills":
            if isinstance(rec, dict) and any(isinstance(v, dict) and v.get("status") in {"installed", "updated", "present"} for v in rec.values()):
                out.append("skills")
            continue
        if isinstance(rec, dict) and rec.get("status") in {"installed", "updated", "present"}:
            out.append(str(name))
    return out


def log_stage(store: Store, stage: str, actor: str, *, detail: dict | None = None) -> None:
    """Append a funnel stage event. Kind == stage; detail carries only anonymous counters."""
    stage = (stage or "").strip()
    if stage not in _LIFECYCLE_ORDER:
        return
    blob: dict[str, Any] = {"stage": stage}
    if detail:
        if detail.get("auto") is True:
            blob["auto"] = True
        if detail.get("offline") is True:
            blob["offline"] = True
        surfaces = detail.get("surfaces")
        if isinstance(surfaces, list):
            clean = [str(s)[:40] for s in surfaces if isinstance(s, str) and s][:20]
            if clean:
                blob["surfaces"] = clean
                blob["n_surfaces"] = len(clean)
        n = detail.get("n_surfaces")
        if isinstance(n, int) and n >= 0 and "n_surfaces" not in blob:
            blob["n_surfaces"] = n
    store.log(stage, (actor or "").strip() or "did:claimidx:anon", "", blob)


def lifecycle_funnel(store: Store, *, days: int = 30) -> dict[str, Any]:
    """DID lifecycle funnel from the local event log (no network, no PII beyond DID).

    Stages: install -> init -> ask -> sync -> confirm -> publish -> share.
    `countable` excludes seed/anon so Growth can see stranger drop-off before a commons share.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = _rows(store, since)
    stages: dict[str, dict[str, Any]] = {s: {"events": 0, "actors": 0, "actor_ids": []} for s in _LIFECYCLE_ORDER}
    actors_by: dict[str, set[str]] = {s: set() for s in _LIFECYCLE_ORDER}
    for r in rows:
        stage = _LIFECYCLE_KINDS.get(r["kind"])
        if not stage:
            continue
        stages[stage]["events"] += 1
        actor = r["actor"]
        if actor and actor not in _FUNNEL_EXCLUDED:
            actors_by[stage].add(actor)
    for stage, actors in actors_by.items():
        ordered = sorted(actors)
        stages[stage]["actors"] = len(ordered)
        stages[stage]["actor_ids"] = ordered[:20]

    # Max stage each countable actor reached (for drop-off).
    reached: dict[str, int] = {}
    for i, stage in enumerate(_LIFECYCLE_ORDER):
        for actor in actors_by[stage]:
            reached[actor] = max(reached.get(actor, -1), i)
    dropoff: dict[str, int] = {}
    for i, stage in enumerate(_LIFECYCLE_ORDER[:-1]):
        nxt = _LIFECYCLE_ORDER[i + 1]
        n = sum(1 for actor, idx in reached.items() if idx == i)
        dropoff[f"{stage}_no_{nxt}"] = n

    countable = sorted(reached)
    return {
        "days": days,
        "order": list(_LIFECYCLE_ORDER),
        "stages": stages,
        "countable_actors": len(countable),
        "countable_actor_ids": countable[:20],
        "dropoff": dropoff,
    }


# COO/Growth shorthand: confirm ≈ local hold; publish ≈ claim; share ≈ commons push.
_SCOREBOARD_ALIAS = {
    "confirm": "hold",
    "publish": "claim",
}


def render_funnel_scoreboard(life: dict[str, Any], *, commons: dict[str, Any] | None = None, proxy: dict[str, Any] | None = None, db: str = "") -> str:
    """Human daily scoreboard from lifecycle_funnel; optional commons event funnel + ledger proxy."""
    if life.get("error"):
        return f"# funnel error: {life['error']}"
    days = life.get("days", 30)
    order = life.get("order") or list(_LIFECYCLE_ORDER)
    stages = life.get("stages") or {}
    lines = [
        f"# funnel {days}d — local home event log only (not on commons / public ledger)",
    ]
    if db:
        lines.append(f"# db {db}")
    lines.append(f"countable DIDs (excl. seed/anon): {life.get('countable_actors', 0)}")
    lines.append("stages (actors / events):")
    for s in order:
        st = stages.get(s) or {}
        alias = _SCOREBOARD_ALIAS.get(s)
        label = f"{s} (≈{alias})" if alias else s
        lines.append(f"  {label:<18} actors={st.get('actors', 0):<5} events={st.get('events', 0)}")
    drop = life.get("dropoff") or {}
    if drop:
        lines.append("drop-off (actors whose max stage is this one):")
        for s in order[:-1]:
            nxt = order[order.index(s) + 1]
            key = f"{s}_no_{nxt}"
            lines.append(f"  {key:<24} {drop.get(key, 0)}")
    # COO path shorthand line: install→init→ask→hold→claim using aliases where present
    path_bits = []
    for s in ("install", "init", "ask", "confirm", "publish"):
        if s not in order:
            continue
        name = _SCOREBOARD_ALIAS.get(s, s)
        n = (stages.get(s) or {}).get("actors", 0)
        path_bits.append(f"{name} {n}")
    if path_bits:
        lines.append("COO path actors: " + " → ".join(path_bits))
    if commons and not commons.get("error"):
        pushes = commons.get("pushes") or {}
        push_n = pushes.get("count", 0) if isinstance(pushes, dict) else 0
        first = commons.get("first_share_actors") or {}
        first_n = first.get("count", 0) if isinstance(first, dict) else 0
        lines.append(
            f"commons funnel (same local log): push {push_n} refuse {commons.get('refused', 0)} "
            f"skip {commons.get('skipped', 0)} hold {commons.get('holds', 0)} fail {commons.get('fails', 0)} "
            f"first-share {first_n}"
        )
    if proxy and not proxy.get("error"):
        goal = proxy.get("countable_goal", COUNTABLE_GOAL)
        lines.append(
            f"commons proxy (public ledger): countable {proxy.get('countable', 0)}/{goal} "
            f"held {proxy.get('held', 0)} confirmed {proxy.get('confirmed', 0)} "
            f"recent_published {proxy.get('recent_published', 0)} "
            f"(claims {proxy.get('claims', 0)}; excl. owners seen {proxy.get('excluded_owners_seen', 0)})"
        )
        lines.append(f"# proxy limits: {proxy.get('limits', 'commons-derived; not install->init')}")
    elif proxy and proxy.get("error"):
        lines.append(f"commons proxy error: {proxy.get('error')}")
    return "\n".join(lines)


def impact(store: Store, *, days: int = 7, own: str = "", offline: bool = False, url: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"own": own, **local_impact(store, days=days, own=own)}
    from .home import commons_enabled

    try:
        out["lifecycle"] = lifecycle_funnel(store, days=days)
    except Exception as e:  # local-only; keep parity with optional bits
        out["lifecycle"] = {"error": str(e)[:200]}
    if commons_enabled():
        try:
            out["funnel"] = commons_funnel(store, days=days)
        except Exception as e:  # local-only; keep parity with optional bits
            out["funnel"] = {"error": str(e)[:200]}
    if not offline and own and own != "did:claimidx:anon":
        try:
            out["public"] = ledger_impact(own, url=url, store=store)
        except Exception as e:  # network is optional here
            out["public"] = {"error": str(e)[:200]}
        if commons_enabled():
            try:
                out["commons"] = commons_impact(own)
            except Exception as e:
                out["commons"] = {"error": str(e)[:200]}
    out["line"] = render_line(out)
    return out


def render_line(out: dict[str, Any]) -> str:
    d = out.get("days", 7)
    bits = [
        f"asks {out.get('asks', 0)}",
        f"hits {out.get('hits', 0)}",
        f"retries skipped {out.get('retries_skipped', 0)}",
        f"claims published {out.get('claims_published', 0)}",
        f"replays held {out.get('replays_held', 0)}",
    ]
    if out.get("hits_that_failed"):
        bits.append(f"hits that failed {out['hits_that_failed']}")
    pub = out.get("public") or {}
    if pub and not pub.get("error"):
        bits.append(
            f"ledger: {pub.get('your_claims', 0)} of your claims, confirmed by others {pub.get('confirmed_by_others', 0)}, replayed {pub.get('replayed_by_others', 0)}"
        )
    com = out.get("commons") or {}
    if com and not com.get("error"):
        rank = f", rank {com['rank']}" if com.get("rank") else ""
        bits.append(
            f"commons {com.get('days', 30)}d: held by others {com.get('held_by_others', 0)} ({com.get('verifiers', 0)} verifiers{rank}), you held {com.get('you_held', 0)}"
        )
    life = out.get("lifecycle") or {}
    if life and not life.get("error"):
        stages = life.get("stages") or {}
        bits.append(
            "lifecycle: "
            + " -> ".join(f"{s} {(stages.get(s) or {}).get('actors', 0)}" for s in (life.get("order") or _LIFECYCLE_ORDER))
            + f" countable {life.get('countable_actors', 0)}"
        )
    funnel = out.get("funnel") or {}
    if funnel and not funnel.get("error"):
        pushes = funnel.get("pushes") or {}
        push_n = pushes.get("count", 0) if isinstance(pushes, dict) else 0
        first = funnel.get("first_share_actors") or {}
        first_n = first.get("count", 0) if isinstance(first, dict) else 0
        bits.append(f"funnel: push {push_n} refuse {funnel.get('refused', 0)} skip {funnel.get('skipped', 0)} first-share {first_n}")
    return f"# impact {d}d: " + ", ".join(bits)
