import json
from collections import Counter
from pathlib import Path

from claimidx.fingerprint import fingerprint
from claimidx.models import Claim
from claimidx.public import public_eval

DATA = Path(__file__).resolve().parents[1] / "data"
LEDGER = DATA / "claims.jsonl"
SELF_LEDGER = DATA / "claims-claimidx.jsonl"
RETIRED_LEDGER = DATA / "claims-retired.jsonl"


def _rows(path: Path = LEDGER) -> list[Claim]:
    out: list[Claim] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(Claim.model_validate(json.loads(line)))
    return out


def test_self_ledger_is_valid_and_disjoint():
    """Claims about this repo live beside the public ledger, not in it."""
    pub = _rows()
    for side in (SELF_LEDGER, RETIRED_LEDGER):
        own = _rows(side)
        assert own, side
        assert not ({c.id for c in pub} & {c.id for c in own}), side
        assert not ({c.fp for c in pub} & {c.fp for c in own}), side
        for c in own:
            assert c.fp == fingerprint(err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=c.dep), c.id


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
