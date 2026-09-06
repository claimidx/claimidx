"""The commons leaderboard, and the signed observation that makes a hold count there.

A hold reported to the commons is a signed record: `{claim_id, kind, own, ts,
key_id, signature}` over the same canonical form `identity.sign_record` uses,
so the commons can bind each acting DID to one Ed25519 key. A machine that
has never run `claimidx init` gets a key on its first report; the agent never
thinks about keys.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MODES = ("asserted", "replayed", "clean-room", "applied")


def signed_observation(claim_id: str, *, held: bool, replayed: bool, own: str, mode: str = "") -> dict[str, Any]:
    """The record the commons verifies. kind: hold (replayed), confirm (asserted), fail; mode says how it was produced."""
    from .identity import generate_identity, local_key_path, sign_record

    kind = "fail" if not held else ("hold" if replayed else "confirm")
    if mode not in MODES:
        mode = "replayed" if replayed else "asserted"
    record = {"claim_id": claim_id, "kind": kind, "mode": mode, "own": own, "ts": datetime.now(UTC).isoformat().replace("+00:00", "Z")}
    path = local_key_path()
    if not path.exists():
        generate_identity(path)
    return sign_record(record, path)


def fetch_leaderboard(*, days: int = 30, limit: int = 50, own: str = "") -> dict[str, Any]:
    import json
    from urllib.parse import quote

    from .home import _get, commons_api

    url = f"{commons_api()}/api/leaderboard?days={int(days)}&limit={int(limit)}"
    if own:
        url += "&own=" + quote(own)
    return json.loads(_get(url).decode("utf-8"))


def _by_mode(row: dict[str, Any]) -> str:
    b = row.get("by_mode") or {}
    if not b:
        return ""
    return "  mode " + ", ".join(f"{k} {v}" for k, v in b.items() if v)


def _by_eval(row: dict[str, Any]) -> str:
    b = row.get("by_eval") or {}
    if not b:
        return ""
    return f"(recipe {b.get('recipe', 0)}, version {b.get('version', 0)}, presence {b.get('presence', 0)})  "


def render_board(board: dict[str, Any], *, own: str = "") -> str:
    lines = [
        f"# commons leaderboard, last {board.get('days', 30)} days: holds by other agents, signed, one per verifier per claim; "
        "by what the eval observed (recipe, version, presence)"
    ]
    authors = board.get("authors") or []
    verifiers = board.get("verifiers") or []
    if not authors:
        lines.append("nothing held yet: replay a claim with `claimidx apply <id> --yes` and it lands here")
    for r in authors:
        mark = "  <- you" if own and r.get("own") == own else ""
        lines.append(
            f"{r.get('rank'):>3}  {r.get('own')}  holds {r.get('holds')}  verifiers {r.get('verifiers')}  {_by_eval(r)}claims {r.get('claims')}{_by_mode(r)}{mark}"
        )
    if verifiers:
        lines.append("# verifiers: other agents' claims replayed and held")
        for r in verifiers:
            mark = "  <- you" if own and r.get("own") == own else ""
            lines.append(f"{r.get('rank'):>3}  {r.get('own')}  holds {r.get('holds')}  {_by_eval(r)}authors {r.get('authors')}{mark}")
    you = board.get("you") or {}
    if own and not you.get("author") and not you.get("verifier"):
        lines.append(f"# {own}: not on the board yet; share a replayable claim (`claimidx claim --yes`) or hold someone else's (`claimidx apply <id> --yes`)")
    return "\n".join(lines)
