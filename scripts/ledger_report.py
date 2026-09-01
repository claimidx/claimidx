"""Honest numbers for data/claims.jsonl. Run before you cite the ledger.

    python scripts/ledger_report.py            # text
    python scripts/ledger_report.py --json     # machine-readable

Replayable means `eval_proof` (eval.cmd can discriminate held vs miss).
A `true` / `go version` eval is a hint: `confirm --replay` cannot prove it.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claimidx.public import eval_is_proof  # noqa: E402

SELF_TOKENS = ("claimidx",)


def is_self_claim(row: dict) -> bool:
    """A claim about Claimidx itself: changelog, not prior art for other agents."""
    blob = " ".join([row.get("err", ""), json.dumps(row.get("fix", {})), row.get("eval", {}).get("cmd", "")]).lower()
    return any(t in blob for t in SELF_TOKENS)


def report(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    n = len(rows)
    proof = sum(1 for r in rows if eval_is_proof(r.get("eval", {}).get("cmd", "")))
    st = Counter(r.get("st") for r in rows)
    src = Counter(r.get("src") for r in rows)
    eco = Counter(r.get("eco") for r in rows)
    own = Counter(r.get("own") for r in rows)
    self_n = sum(1 for r in rows if is_self_claim(r))
    placeholder = sum(1 for r in rows if "<STR>" in r.get("err", "") or "<N>" in r.get("err", ""))
    return {
        "n": n,
        "replayable": proof,
        "replayable_pct": round(100 * proof / n, 1) if n else 0.0,
        "hint_evals": n - proof,
        "confirmed": st.get("confirmed", 0),
        "confirmed_non_seed": sum(1 for r in rows if r.get("st") == "confirmed" and r.get("src") != "seed"),
        "proposed": st.get("proposed", 0),
        "contested": st.get("contested", 0),
        "self_referential": self_n,
        "placeholder_errs": placeholder,
        "top_owner": own.most_common(1)[0] if own else None,
        "owners": len(own),
        "src": dict(src),
        "eco": dict(eco),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ledger", default=str(ROOT / "data" / "claims.jsonl"))
    ap.add_argument("--json", action="store_true")
    ns = ap.parse_args(argv)
    r = report(Path(ns.ledger))
    if ns.json:
        print(json.dumps(r, indent=2))
        return 0
    print(f"claims            {r['n']}")
    print(f"replayable        {r['replayable']} ({r['replayable_pct']}%)  hint evals: {r['hint_evals']}")
    print(f"confirmed         {r['confirmed']} (non-seed: {r['confirmed_non_seed']})  proposed: {r['proposed']}  contested: {r['contested']}")
    print(f"self-referential  {r['self_referential']}")
    print(f"placeholder errs  {r['placeholder_errs']} (<STR>/<N> in err)")
    if r["top_owner"]:
        print(f"owners            {r['owners']} (top: {r['top_owner'][0]} = {r['top_owner'][1]})")
    print(f"eco               {r['eco']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
