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
        "verifiers": int(author.get("verifiers") or 0),
        "rank": author.get("rank"),
        "you_held": int(verifier.get("holds") or 0),
        "verifier_rank": verifier.get("rank"),
        "authors_on_board": len(board.get("authors") or []),
    }


def impact(store: Store, *, days: int = 7, own: str = "", offline: bool = False, url: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"own": own, **local_impact(store, days=days, own=own)}
    if not offline and own and own != "did:claimidx:anon":
        try:
            out["public"] = ledger_impact(own, url=url, store=store)
        except Exception as e:  # network is optional here
            out["public"] = {"error": str(e)[:200]}
        from .home import commons_enabled

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
    return f"# impact {d}d: " + ", ".join(bits)
