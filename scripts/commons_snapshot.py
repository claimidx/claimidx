"""Snapshot the commons into data/claims.jsonl, the offline fallback `claimidx pull` reads.

    python scripts/commons_snapshot.py            # write data/claims.jsonl from the commons export
    python scripts/commons_snapshot.py --check    # exit 1 when the snapshot is behind

Rows are validated as Claim, kept in commons order, written with LF line endings.
The workflow commons-snapshot.yml runs this daily and commits when the file changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claimidx.home import COMMONS_LEDGER, _get  # noqa: E402
from claimidx.models import Claim  # noqa: E402

TARGET = ROOT / "data" / "claims.jsonl"


def fetch(url: str = COMMONS_LEDGER) -> list[str]:
    lines: list[str] = []
    skipped = 0
    for raw in _get(url, timeout=60).decode("utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
            Claim.model_validate(row)
        except Exception:
            skipped += 1
            continue
        lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    if skipped:
        print(f"skipped {skipped} rows that do not validate", file=sys.stderr)
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--url", default=COMMONS_LEDGER)
    ns = ap.parse_args()
    lines = fetch(ns.url)
    if not lines:
        print("the commons returned no rows; keeping the current snapshot", file=sys.stderr)
        return 1
    body = "\n".join(lines) + "\n"
    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    if ns.check:
        print("snapshot is current" if current == body else f"snapshot is behind: {len(lines)} rows on the commons")
        return 0 if current == body else 1
    TARGET.write_text(body, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET} ({len(lines)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
