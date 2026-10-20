"""Apply the prune rule to the commons itself: retire rows whose eval cannot prove their failure, upgrade the rest.

    python scripts/commons_prune.py            # report what would change
    python scripts/commons_prune.py --apply    # DELETE retired rows, UPDATE upgraded evals, via wrangler d1

Same rule as claimidx.prune (`upgrade_eval`): proof after upgrade, runnable by the policy, whole in the
public projection, and about the failure. Needs wrangler and the Cloudflare env. Retired rows are written
to data/claims-retired.jsonl first so nothing is lost.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from claimidx.home import COMMONS_LEDGER, _get  # noqa: E402
from claimidx.models import Claim  # noqa: E402
from claimidx.prune import upgrade_eval  # noqa: E402

WORKER = ROOT / "workers" / "home"
DB = "claimidx-cloud"
RETIRED = ROOT / "data" / "claims-retired.jsonl"
BATCH = 60


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def wrangler(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(["npx", "--yes", "wrangler", *args], cwd=str(WORKER), capture_output=True, text=True, check=False, shell=(sys.platform == "win32"))


def run_sql(statements: list[str], label: str) -> bool:
    for i in range(0, len(statements), BATCH):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as fh:
            fh.write("\n".join(statements[i : i + BATCH]) + "\n")
            path = fh.name
        r = wrangler(["d1", "execute", DB, "--remote", "--file", path])
        Path(path).unlink(missing_ok=True)
        if r.returncode != 0:
            print(f"{label} batch {i // BATCH + 1}: FAILED\n{r.stdout[-400:]}\n{r.stderr[-600:]}", file=sys.stderr)
            return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true")
    ns = ap.parse_args()
    rows = [json.loads(ln) for ln in _get(COMMONS_LEDGER, timeout=60).decode("utf-8").splitlines() if ln.strip()]
    retire: list[dict] = []
    upgrade: list[tuple[dict, str]] = []
    kept = 0
    for raw in rows:
        try:
            claim = Claim.model_validate(raw)
        except Exception:
            retire.append(raw)
            continue
        ev, how = upgrade_eval(claim)
        if how == "hint":
            retire.append(raw)
        elif how != "kept":
            upgrade.append((raw, ev))
        else:
            kept += 1
    already = set()
    if RETIRED.exists():
        for ln in RETIRED.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                already.add(json.loads(ln).get("id"))
    new_retired = [r for r in retire if r.get("id") not in already]
    print(
        json.dumps(
            {"commons_rows": len(rows), "kept": kept, "upgrade": len(upgrade), "retire": len(retire), "retire_not_yet_in_retired_file": len(new_retired)}
        )
    )
    if not ns.apply:
        return 0
    if new_retired:
        with RETIRED.open("a", encoding="utf-8", newline="\n") as fh:
            for r in new_retired:
                r = dict(r)
                r["note"] = ((r.get("note") or "") + " [retired from the commons: eval does not prove the failure]").strip()
                fh.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    deletes = [f"DELETE FROM claims WHERE tenant='commons' AND id={q(str(r['id']))};" for r in retire]
    updates = []
    for raw, ev in upgrade:
        new = dict(raw)
        new["eval"] = {"cmd": ev, "expect": int((raw.get("eval") or {}).get("expect") or 0)}
        updates.append(
            f"UPDATE claims SET json={q(json.dumps(new, ensure_ascii=False, separators=(',', ':')))} WHERE tenant='commons' AND id={q(str(raw['id']))};"
        )
    if deletes and not run_sql(deletes, "delete"):
        return 1
    if updates and not run_sql(updates, "update"):
        return 1
    print(f"commons: deleted {len(deletes)}, updated {len(updates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
