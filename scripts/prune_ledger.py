"""Prune the public ledger and the bundled seeds to claims that can graduate.

    python scripts/prune_ledger.py            # report only
    python scripts/prune_ledger.py --apply    # rewrite data/claims.jsonl, append retired rows, regenerate seed_data.py

Rule (claimidx.prune): a claim stays when its eval is proof after the
upgrade attempt (pin -> version check, missing module -> import, Go
package -> `go list`, crate -> `cargo pkgid`). Everything else is a note,
not prior art, and is retired to data/claims-retired.jsonl. Seed counters
were written by hand and are dropped; a seed starts at zero like any claim.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claimidx.prune import prune_ledger, prune_seed_rows  # noqa: E402

SEED_FILE = ROOT / "src" / "claimidx" / "seed_data.py"
_KEY_ORDER = ["id", "err", "eco", "rt", "dep", "tool", "tried", "fix", "eval", "note"]


def _emit_seed(row: dict) -> str:
    keys = [k for k in _KEY_ORDER if k in row] + [k for k in row if k not in _KEY_ORDER]
    body = "".join(f"        {json.dumps(k)}: {row[k]!r},\n" for k in keys)
    return "    {\n" + body + "    },\n"


def regenerate_seeds(apply: bool) -> dict:
    from claimidx import seed_data

    rows = list(seed_data._SEEDS)
    kept, report = prune_seed_rows(rows)
    src = SEED_FILE.read_text(encoding="utf-8")
    head, _, rest = src.partition("_SEEDS: list[dict] = [\n")
    _, _, tail = rest.partition("\ndef materialize(")
    new = head + "_SEEDS: list[dict] = [\n" + "".join(_emit_seed(r) for r in kept) + "]\n\n\ndef materialize(" + tail
    new = re.sub(r"\n{3,}(def materialize)", r"\n\n\n\1", new)
    if apply:
        SEED_FILE.write_text(new, encoding="utf-8", newline="\n")
        subprocess.run([sys.executable, "-m", "ruff", "format", str(SEED_FILE)], check=False, capture_output=True)
    out = report.as_dict()
    out["kept_rows"] = len(kept)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--ledger", default=str(ROOT / "data" / "claims.jsonl"))
    ap.add_argument("--retired", default=str(ROOT / "data" / "claims-retired.jsonl"))
    ap.add_argument("--no-seeds", action="store_true")
    ns = ap.parse_args()
    ledger = prune_ledger(Path(ns.ledger), Path(ns.retired), apply=ns.apply).as_dict()
    out = {"applied": ns.apply, "ledger": ledger}
    if not ns.no_seeds:
        out["seeds"] = regenerate_seeds(ns.apply)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
