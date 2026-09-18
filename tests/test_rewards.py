"""Monthly contributor standing: flat, deterministic, one row per owner, computed from the ledger alone."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from claimidx.cli import main
from claimidx.fingerprint import fingerprint
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.rewards import eligible, month_window, previous_month

NOW = datetime(2026, 9, 20, tzinfo=UTC)


def _c(err: str, own: str, ts: str, *, st: str = "confirmed", nf: int = 0, nc: int = 1, eco: str = "py", fix: str = "x==1") -> Claim:
    return Claim(
        fp=fingerprint(err=err, eco=eco, dep=[fix]),
        cls="module_not_found",
        err=err,
        eco=eco,
        dep=[fix],
        fix=Fix(k="pin", b=fix),
        eval=EvalSpec(cmd='python -c "import x"'),
        st=st,
        nc=nc,
        nf=nf,
        own=own,
        ts=datetime.fromisoformat(ts.replace("Z", "+00:00")),
    )


def test_month_window_and_previous_month():
    assert month_window("2026-12") == (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC))
    assert previous_month(NOW) == "2026-08"


def test_one_row_per_owner_with_every_skip_reason(monkeypatch):
    monkeypatch.delenv("CLAIMIDX_REWARDS_EXCLUDE", raising=False)
    a1 = _c("ModuleNotFoundError: No module named 'a'", "did:claimidx:alice", "2026-08-03T00:00:00Z")
    a2 = _c("ModuleNotFoundError: No module named 'b'", "did:claimidx:alice", "2026-08-10T00:00:00Z")  # volume earns nothing extra
    bob = _c("ModuleNotFoundError: No module named 'c'", "did:claimidx:bob", "2026-08-15T00:00:00Z", st="proposed")
    carol = _c("ModuleNotFoundError: No module named 'd'", "did:claimidx:carol", "2026-08-16T00:00:00Z", nf=1)
    dave_dup = _c("ModuleNotFoundError: No module named 'a'", "did:claimidx:dave", "2026-08-20T00:00:00Z")  # same fp as a1, later
    erin_near = _c("ModuleNotFoundError: No module named 'a'", "did:claimidx:erin", "2026-08-21T00:00:00Z", fix="x==2")  # same family, other fp
    seed = _c("ModuleNotFoundError: No module named 'e'", "did:claimidx:seed", "2026-08-22T00:00:00Z")
    op = _c("ModuleNotFoundError: No module named 'f'", "did:claimidx:operator", "2026-08-23T00:00:00Z")
    july = _c("ModuleNotFoundError: No module named 'g'", "did:claimidx:frank", "2026-07-30T00:00:00Z")
    contested = _c("ModuleNotFoundError: No module named 'h'", "did:claimidx:gina", "2026-08-24T00:00:00Z", st="contested")
    rep = eligible(
        [a1, a2, bob, carol, dave_dup, erin_near, seed, op, july, contested], month="2026-08", now=NOW, exclude={"did:claimidx:seed", "did:claimidx:operator"}
    )
    assert rep["n_eligible"] == 1 and rep["eligible"][0]["own"] == "did:claimidx:alice"
    assert [c["id"] for c in rep["eligible"][0]["claims"]] == [a1.id, a2.id]
    assert rep["skipped"] == {"disputed": 1, "duplicate": 1, "excluded": 2, "near-duplicate": 1, "not-confirmed": 1, "retired-or-contested": 1}
    assert rep["provisional"] is False and rep["cutoff"] == "2026-09-15T00:00:00Z"
    # Same ledger, same cutoff: same answer.
    assert (
        eligible(
            [a1, a2, bob, carol, dave_dup, erin_near, seed, op, july, contested],
            month="2026-08",
            now=NOW,
            exclude={"did:claimidx:seed", "did:claimidx:operator"},
        )
        == rep
    )


def test_window_still_open_is_provisional_and_recent_claims_wait():
    fresh = _c("ModuleNotFoundError: No module named 'z'", "did:claimidx:zed", "2026-08-30T00:00:00Z")
    rep = eligible([fresh], month="2026-08", now=datetime(2026, 9, 5, tzinfo=UTC), exclude=set())
    assert rep["provisional"] is True and rep["n_eligible"] == 0 and rep["skipped"] == {"window-open": 1}
    later = eligible([fresh], month="2026-08", now=NOW, exclude=set())
    assert later["n_eligible"] == 1 and later["provisional"] is False


def test_exclusions_come_from_env_config_and_flags(monkeypatch, tmp_path: Path):
    from claimidx.rewards import excluded_owners

    monkeypatch.setenv("CLAIMIDX_REWARDS_EXCLUDE", "did:claimidx:env-a, did:claimidx:env-b")
    (tmp_path / "config.json").write_text(json.dumps({"rewards_exclude": ["did:claimidx:cfg"]}), encoding="utf-8")
    ex = excluded_owners(["did:claimidx:flag"])
    assert {"did:claimidx:seed", "did:claimidx:anon", "did:claimidx:env-a", "did:claimidx:env-b", "did:claimidx:cfg", "did:claimidx:flag"} <= ex


def test_cli_rewards_reads_a_local_ledger(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_REWARDS_EXCLUDE", raising=False)
    a = _c("ModuleNotFoundError: No module named 'a'", "did:claimidx:alice", "2026-08-03T00:00:00Z")
    ledger = tmp_path / "claims.jsonl"
    ledger.write_text(a.model_dump_json() + "\n", encoding="utf-8")
    assert main(["--fmt", "json", "rewards", "--month", "2026-08", "--ledger", str(ledger), "--now", "2026-09-20T00:00:00Z"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["n_eligible"] == 1 and out["eligible"][0]["own"] == "did:claimidx:alice" and out["ledger"] == str(ledger)
    assert main(["rewards", "--month", "2026-08", "--ledger", str(ledger), "--now", "2026-09-20T00:00:00Z", "--exclude", "did:claimidx:alice"]) == 0
    text = capsys.readouterr().out
    assert "0 eligible" in text and "excluded 1" in text
