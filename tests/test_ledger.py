import json
from collections import Counter
from pathlib import Path

from claimidx.fingerprint import fingerprint
from claimidx.models import Claim
from claimidx.public import public_eval

LEDGER = Path(__file__).resolve().parents[1] / "data" / "claims.jsonl"


def _rows() -> list[Claim]:
    out: list[Claim] = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Claim.model_validate(json.loads(line)))
    return out


def test_public_ledger_ids_and_fingerprints_are_unique_and_current():
    rows = _rows()
    assert len(rows) >= 12
    ids = [c.id for c in rows]
    fps = [c.fp for c in rows]
    assert len(ids) == len(set(ids))
    assert len(fps) == len(set(fps))
    for c in rows:
        got = fingerprint(err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=c.dep)
        assert c.fp == got, c.id


def test_public_ledger_is_projected():
    srcs = Counter()
    for c in _rows():
        srcs[c.src] += 1
        assert c.src in {"seed", "home"}, c.id
        assert public_eval(c.eval.cmd) == c.eval.cmd, c.id
        assert "tests/" not in c.eval.cmd, c.id
    assert srcs["home"] + srcs["seed"] == sum(srcs.values())
