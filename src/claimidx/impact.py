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
    with store._conn() as con:
        rows = con.execute("SELECT claim_id, kind, actor, ts, detail FROM events ORDER BY id ASC").fetchall()
    out: list[dict] = []
    for r in rows:
        when = _ts(r["ts"])
        if when is None or when < since:
            continue
        detail: dict = {}
        raw = r["detail"] if "detail" in r.keys() else None
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


def impact(store: Store, *, days: int = 7, own: str = "", offline: bool = False, url: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"own": own, **local_impact(store, days=days, own=own)}
    from .home import commons_enabled

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
    funnel = out.get("funnel") or {}
    if funnel and not funnel.get("error"):
        pushes = funnel.get("pushes") or {}
        push_n = pushes.get("count", 0) if isinstance(pushes, dict) else 0
        first = funnel.get("first_share_actors") or {}
        first_n = first.get("count", 0) if isinstance(first, dict) else 0
        bits.append(f"funnel: push {push_n} refuse {funnel.get('refused', 0)} skip {funnel.get('skipped', 0)} first-share {first_n}")
    return f"# impact {d}d: " + ", ".join(bits)
