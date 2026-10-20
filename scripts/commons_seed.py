"""Seed the commons (the open tenant on the home worker) from the repo's public ledger.

    python scripts/commons_seed.py --dry-run        # print the SQL batch count
    python scripts/commons_seed.py                  # create the tenant row if missing, then upsert data/claims.jsonl

Needs wrangler and CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID in the environment
(~/.claimidx/cloudflare.env on the operator desktop). Rows keep their id, owner,
counters and timestamp; `INSERT OR IGNORE` so a re-run never clobbers what agents
have since replayed on the commons.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers" / "home"
LEDGER = ROOT / "data" / "claims.jsonl"
DB = "claimidx-cloud"
BATCH = 80


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def tenant_sql() -> str:
    now = datetime.now(UTC).isoformat()
    return f"INSERT OR IGNORE INTO tenants (slug,email,sku,did_cap,token_hash,status,created) VALUES ('commons','','commons',1000000000,'','live',{q(now)});"


def row_sql(raw: dict) -> str:
    ev = raw.get("eval") or {}
    fix = raw.get("fix") or {}
    claim = dict(raw)
    claim["src"] = "home"
    claim.setdefault("nr", 0)
    return (
        "INSERT OR IGNORE INTO claims (tenant,id,fp,cls,eco,json,nc,nf,st,ts,own) VALUES ("
        + ",".join(
            [
                "'commons'",
                q(str(raw["id"])),
                q(str(raw["fp"])),
                q(str(raw.get("cls") or "other")),
                q(str(raw.get("eco") or "other")),
                q(json.dumps(claim, ensure_ascii=False, separators=(",", ":"))),
                str(int(raw.get("nc") or 0)),
                str(int(raw.get("nf") or 0)),
                q(str(raw.get("st") or "proposed")),
                q(str(raw.get("ts") or "")),
                q(str(raw.get("own") or "did:claimidx:anon")),
            ]
        )
        + ");"
        + ("" if isinstance(ev, dict) and isinstance(fix, dict) else "")
    )


def wrangler(args: list[str]) -> subprocess.CompletedProcess:
    cmd = ["npx", "--yes", "wrangler", *args]
    return subprocess.run(cmd, cwd=str(WORKER), capture_output=True, text=True, check=False, shell=(sys.platform == "win32"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ledger", default=str(LEDGER))
    ns = ap.parse_args()
    rows = [json.loads(ln) for ln in Path(ns.ledger).read_text(encoding="utf-8").splitlines() if ln.strip()]
    batches = [rows[i : i + BATCH] for i in range(0, len(rows), BATCH)]
    print(f"{len(rows)} rows in {len(batches)} batches")
    if ns.dry_run:
        return 0
    r = wrangler(["d1", "execute", DB, "--remote", "--command", tenant_sql()])
    if r.returncode != 0:
        print(r.stdout[-500:], r.stderr[-800:], file=sys.stderr)
        return 1
    print("tenant: ok")
    for i, batch in enumerate(batches, 1):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(row_sql(r) for r in batch) + "\n")
            path = fh.name
        r = wrangler(["d1", "execute", DB, "--remote", "--file", path])
        Path(path).unlink(missing_ok=True)
        if r.returncode != 0:
            print(f"batch {i}: FAILED\n{r.stdout[-500:]}\n{r.stderr[-800:]}", file=sys.stderr)
            return 1
        print(f"batch {i}/{len(batches)}: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
