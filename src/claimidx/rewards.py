"""Monthly contributor standing, computed read-only from the public ledger.

Who qualifies in a month: every owner DID with at least one claim that was published in
that month, is confirmed and undisputed, cleared a dispute window, and is neither a
duplicate nor a near-duplicate (same failure family) of an older claim. One row per owner
however many claims qualify, so volume earns nothing extra. Operator and seed identities
are excluded. Anyone can re-run this against the same ledger and get the same answer.

The program terms (what a qualifying month is worth, caps, how it is applied) live with
the operator; this module only says who qualified and why the rest did not.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from .fingerprint import family_fingerprint
from .models import Claim

ALWAYS_EXCLUDED = frozenset({"did:claimidx:seed", "did:claimidx:anon"})
DEFAULT_WINDOW_DAYS = 14


def month_window(month: str) -> tuple[datetime, datetime]:
    """`YYYY-MM` -> [start, end) in UTC."""
    try:
        year, mon = (int(x) for x in month.split("-", 1))
        start = datetime(year, mon, 1, tzinfo=UTC)
    except (ValueError, TypeError) as e:
        raise ValueError(f"month must be YYYY-MM, got {month!r}") from e
    end = datetime(year + (mon // 12), (mon % 12) + 1, 1, tzinfo=UTC)
    return start, end


def previous_month(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    first = now.replace(day=1)
    last = first - timedelta(days=1)
    return f"{last.year:04d}-{last.month:02d}"


def excluded_owners(extra: list[str] | None = None) -> set[str]:
    """Seed/anon always; `CLAIMIDX_REWARDS_EXCLUDE` and config `rewards_exclude` (comma or list) on top."""
    out = set(ALWAYS_EXCLUDED)
    env = os.environ.get("CLAIMIDX_REWARDS_EXCLUDE") or ""
    out.update(x.strip() for x in env.split(",") if x.strip())
    try:
        from .config import get as cfg_get

        cfg = cfg_get("rewards_exclude", None)
    except Exception:
        cfg = None
    if isinstance(cfg, str):
        out.update(x.strip() for x in cfg.split(",") if x.strip())
    elif isinstance(cfg, (list, tuple)):
        out.update(str(x).strip() for x in cfg if str(x).strip())
    out.update(x.strip() for x in (extra or []) if x and x.strip())
    return out


def _ts(claim: Claim) -> datetime:
    ts = claim.ts
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def excluded_before(claims: list[Claim], date: str) -> set[str]:
    """Owners with any claim published before `date` (YYYY-MM-DD): identities that were on the ledger
    before a program started, which is how an operator excludes its own agents without keeping a list."""
    try:
        cutoff = datetime.fromisoformat(date).replace(tzinfo=UTC)
    except (ValueError, TypeError) as e:
        raise ValueError(f"exclude_before must be YYYY-MM-DD, got {date!r}") from e
    return {c.own for c in claims if _ts(c) < cutoff}


def exclude_before_setting() -> str:
    """`CLAIMIDX_REWARDS_EXCLUDE_BEFORE`, else config `rewards_exclude_before`, else ""."""
    env = (os.environ.get("CLAIMIDX_REWARDS_EXCLUDE_BEFORE") or "").strip()
    if env:
        return env
    try:
        from .config import get as cfg_get

        return str(cfg_get("rewards_exclude_before", "") or "").strip()
    except Exception:
        return ""


def eligible(
    claims: list[Claim],
    *,
    month: str,
    now: datetime | None = None,
    window_days: int = DEFAULT_WINDOW_DAYS,
    exclude: set[str] | None = None,
    exclude_before: str | None = None,
) -> dict[str, Any]:
    """Standing for one month. Deterministic: same ledger, same month, same cutoff -> same answer."""
    now = now or datetime.now(UTC)
    if not now.tzinfo:
        now = now.replace(tzinfo=UTC)
    start, end = month_window(month)
    excluded = set(exclude if exclude is not None else excluded_owners())
    before = exclude_before if exclude_before is not None else exclude_before_setting()
    if before:
        excluded |= excluded_before(claims, before)
    cutoff = end + timedelta(days=window_days)
    # Duplicates: the oldest claim per fingerprint and per failure family is the original.
    ordered = sorted(claims, key=lambda c: (_ts(c), c.id))
    first_fp: dict[str, str] = {}
    first_family: dict[str, str] = {}
    for c in ordered:
        first_fp.setdefault(c.fp, c.id)
        first_family.setdefault(family_fingerprint(err=c.err, cls=c.cls, eco=c.eco), c.id)
    by_owner: dict[str, list[dict[str, Any]]] = {}
    skipped: dict[str, int] = {}
    for c in ordered:
        ts = _ts(c)
        if not (start <= ts < end):
            continue
        reason = ""
        if c.own in excluded:
            reason = "excluded"
        elif c.st == "rejected" or c.st == "contested":
            reason = "retired-or-contested"
        elif c.nf > 0:
            reason = "disputed"
        elif c.st != "confirmed":
            reason = "not-confirmed"
        elif first_fp.get(c.fp) != c.id:
            reason = "duplicate"
        elif first_family.get(family_fingerprint(err=c.err, cls=c.cls, eco=c.eco)) != c.id:
            reason = "near-duplicate"
        elif now < ts + timedelta(days=window_days):
            reason = "window-open"
        if reason:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        by_owner.setdefault(c.own, []).append({"id": c.id, "fp": c.fp, "ts": ts.isoformat().replace("+00:00", "Z"), "nc": c.nc, "nr": c.nr})
    rows = [{"own": own, "claims": items} for own, items in sorted(by_owner.items())]
    return {
        "month": month,
        "window_days": window_days,
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "now": now.isoformat().replace("+00:00", "Z"),
        "provisional": now < cutoff,
        "rules": [
            "published in the month",
            "st=confirmed and nf=0",
            "not rejected or contested",
            f"older than {window_days} days at the cutoff",
            "oldest claim for its fingerprint and its failure family",
            "one row per owner; seed, anon, and excluded DIDs never qualify",
            *(["owners already on the ledger before " + before + " are excluded"] if before else []),
        ],
        "exclude_before": before,
        "excluded": sorted(excluded),
        "n_eligible": len(rows),
        "eligible": rows,
        "skipped": dict(sorted(skipped.items())),
    }


def render(report: dict[str, Any]) -> str:
    head = f"# standing {report['month']}: {report['n_eligible']} eligible" + (" (provisional: window still open)" if report.get("provisional") else "")
    lines = [head]
    for row in report["eligible"]:
        lines.append(f"{row['own']}  {' '.join(c['id'] for c in row['claims'])}")
    if report.get("skipped"):
        lines.append("# skipped: " + ", ".join(f"{k} {v}" for k, v in report["skipped"].items()))
    return "\n".join(lines)
