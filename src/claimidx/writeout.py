"""Write-side unverified outcomes: split by how many copies a re-read finds."""

from __future__ import annotations

from typing import Any

WRITE_UNVERIFIED = "write_unverified"
DEDUPE_HELD = "dedupe_held"
WRITE_LOST = "write_lost"
DEDUPE_MISSED = "dedupe_missed"

# Hyphenated write_outcome values stored on event detail (not a Claim field).
OUTCOME_HELD = "dedupe-held"
OUTCOME_LOST = "write-lost"
OUTCOME_MISSED = "dedupe-missed"
OUTCOME_UNVERIFIED = "unverified"

WRITE_OUTCOME_CLASSES = frozenset({WRITE_UNVERIFIED, DEDUPE_HELD, WRITE_LOST, DEDUPE_MISSED})

CLS_TO_OUTCOME = {
    DEDUPE_HELD: OUTCOME_HELD,
    WRITE_LOST: OUTCOME_LOST,
    DEDUPE_MISSED: OUTCOME_MISSED,
    WRITE_UNVERIFIED: OUTCOME_UNVERIFIED,
}

WRITE_NOT_PROOF = "write outcome is not proof (nr not minted); re-read the consumer listing with --net"
STATUS_EVAL_WARN = (
    "eval only checks a status code; re-read the consumer listing "
    "(claimidx confirm --replay --net). A status-code check is not proof, and "
    "write_unverified / write outcomes never mint nr"
)
STATUS_EVAL_CLASSES = frozenset({"http_status_lie"} | set(WRITE_OUTCOME_CLASSES))


def write_outcome(copies: int) -> dict[str, Any]:
    """Map a post-wait re-read copy count to a class, action, and write_outcome tag.

    1 copy is dedupe-held (stop retrying). 0 copies is write-lost (retry).
    2 or more is dedupe-missed. Each class has its own fingerprint because
    `cls` is part of the hash, so a stop-retrying fix never exact-matches a lost write.
    """
    n = int(copies)
    if n <= 0:
        return {
            "cls": WRITE_LOST,
            "write_outcome": OUTCOME_LOST,
            "copies": n,
            "action": "retry",
            "hint": "re-read found 0 copies; the write was lost — retry the write",
        }
    if n == 1:
        return {
            "cls": DEDUPE_HELD,
            "write_outcome": OUTCOME_HELD,
            "copies": 1,
            "action": "stop",
            "hint": "re-read found 1 copy; the write landed and was de-duplicated — stop retrying",
        }
    return {
        "cls": DEDUPE_MISSED,
        "write_outcome": OUTCOME_MISSED,
        "copies": n,
        "action": "review",
        "hint": f"re-read found {n} copies; de-duplication missed — review before retrying",
    }


def outcome_for_cls(cls: str) -> str:
    return CLS_TO_OUTCOME.get((cls or "").strip(), "")
