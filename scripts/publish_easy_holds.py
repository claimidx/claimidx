"""Publish a small pack of easy-to-hold claims to the commons.

These are common ModuleNotFoundError walls with a pin fix and an import eval
a stranger can `claimidx apply <id> --yes` in under a minute. Run from a
machine with CLAIMIDX_OWNER set and commons enabled:

    python scripts/publish_easy_holds.py
    python scripts/publish_easy_holds.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claimidx.team import resolve_owner  # noqa: E402
from claimidx.fingerprint import classify, fingerprint, normalize_error  # noqa: E402
from claimidx.home import share_claim  # noqa: E402
from claimidx.models import Claim, EvalSpec, Fix  # noqa: E402
from claimidx.store import Store  # noqa: E402

# Pack of easy holds: discriminating import evals, pin remedies apply can install.
EASY_HOLDS: list[dict] = [
    {
        "err": "ModuleNotFoundError: No module named 'tomli'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "tomli>=2.0.1"),
        "eval": 'python -c "import tomli"',
        "note": "easy-hold: tomli import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'yaml'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "PyYAML>=6.0"),
        "eval": 'python -c "import yaml"',
        "note": "easy-hold: PyYAML import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'dotenv'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "python-dotenv>=1.0"),
        "eval": 'python -c "import dotenv"',
        "note": "easy-hold: python-dotenv import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'httpx'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "httpx>=0.27"),
        "eval": 'python -c "import httpx"',
        "note": "easy-hold: httpx import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'rich'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "rich>=13"),
        "eval": 'python -c "import rich"',
        "note": "easy-hold: rich import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'orjson'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "orjson>=3.10"),
        "eval": 'python -c "import orjson"',
        "note": "easy-hold: orjson import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'typing_extensions'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "typing_extensions>=4.12"),
        "eval": 'python -c "import typing_extensions"',
        "note": "easy-hold: typing_extensions import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'packaging'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "packaging>=24"),
        "eval": 'python -c "import packaging"',
        "note": "easy-hold: packaging import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'certifi'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "certifi>=2024.0.0"),
        "eval": 'python -c "import certifi"',
        "note": "easy-hold: certifi import gate",
    },
    {
        "err": "ModuleNotFoundError: No module named 'idna'",
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "fix": ("pin", "idna>=3.7"),
        "eval": 'python -c "import idna"',
        "note": "easy-hold: idna import gate",
    },
]


def _claim(raw: dict, own: str) -> Claim:
    err = raw["err"]
    cls = classify(err)
    eco = raw.get("eco") or "py"
    rt = raw.get("rt") or "py@3.12"
    dep = list(raw.get("dep") or [])
    k, b = raw["fix"]
    return Claim(
        fp=fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep),
        cls=cls,
        err=normalize_error(err),
        eco=eco,
        rt=rt,
        dep=dep,
        tried=list(raw.get("tried") or ["pip-install"]),
        fix=Fix(k=k, b=b),
        eval=EvalSpec(cmd=raw["eval"]),
        own=own,
        note=raw.get("note") or "easy-hold",
        src="local",
        st="proposed",
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default="")
    ns = ap.parse_args(argv)
    own = resolve_owner(os.environ.get("CLAIMIDX_OWNER"))
    if not own or own == "did:claimidx:anon":
        print("set CLAIMIDX_OWNER to a real DID", file=sys.stderr)
        return 2
    store = Store(ns.db) if ns.db else Store()
    results = []
    for raw in EASY_HOLDS:
        c = _claim(raw, own)
        existing = next((row for row in store.by_fp(c.fp) if row.own == own), None)
        if existing:
            c = existing
            action = "exists"
        elif ns.dry_run:
            action = "would-ingest"
        else:
            c = store.put(c)
            action = "ingested"
        share_out = {"status": "dry-run"} if ns.dry_run else share_claim(store, c)
        commons = share_out.get("commons") if isinstance(share_out.get("commons"), dict) else share_out
        results.append(
            {
                "action": action,
                "id": c.id,
                "fp": c.fp[:16],
                "err": c.err[:60],
                "fix": c.fix.b,
                "share": commons,
            }
        )
    print(json.dumps({"n": len(results), "own": own, "dry_run": ns.dry_run, "results": results}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
