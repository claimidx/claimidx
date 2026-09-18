"""A claim published without an ecosystem must still recompute its own fingerprint.

The ledger recomputes `fp` from the stored row (`home.parse_ledger`); a row whose fp was hashed with
`eco=""` but stored as `eco="other"` is rejected on every pull, by every consumer.
"""

from __future__ import annotations

import json
from pathlib import Path

from claimidx import home, ingest
from claimidx.cli import main
from claimidx.fingerprint import fingerprint
from claimidx.store import Store

ERR = "ModuleNotFoundError: No module named 'tomli'"


def _recomputes(store: Store, cid: str) -> bool:
    c = store.get(cid)
    assert c is not None
    return fingerprint(err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=c.dep) == c.fp


def test_cli_ingest_without_eco_survives_a_ledger_round_trip(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "--fmt", "json", "ingest", "--err", ERR, "--fix-k", "pin", "--fix-b", "tomli==2.0.1", "--eval", 'python -c "import tomli"']) == 0
    cid = json.loads(capsys.readouterr().out)["id"]
    store = Store(db)
    assert store.get(cid).eco == "other"
    assert _recomputes(store, cid)
    line = home.propose_line(store.get(cid))
    claims, skipped = home.parse_ledger(line + "\n")
    assert [c.id for c in claims] == [cid] and skipped == []


def test_python_ingest_and_ask_agree_on_a_missing_eco(tmp_path: Path):
    db = str(tmp_path / "ix.sqlite")
    out = ingest(ERR, fix_k="pin", fix_b="tomli==2.0.1", eval='python -c "import tomli"', own="did:claimidx:agent-a", db=db)
    store = Store(db)
    assert _recomputes(store, out["id"])
    # An ask that names no ecosystem still lands on the exact fingerprint.
    assert fingerprint(err=ERR, eco="") == fingerprint(err=ERR, eco="other") == store.get(out["id"]).fp
