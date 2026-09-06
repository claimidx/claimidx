"""`impact` answers the only question that keeps the hook installed: was it worth it?"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claimidx.cli import main


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def test_impact_counts_a_saved_retry_and_a_solved_miss(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'json'"
    # A miss, then the agent solves it and publishes: misses_you_solved.
    main(["--db", db, "--fmt", "json", "ask", "--err", err, "--eco", "py", "--rt", _py_rt()])
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                err,
                "--eco",
                "py",
                "--rt",
                _py_rt(),
                "--fix-k",
                "constraint",
                "--fix-b",
                "json",
                "--eval",
                'python -c "import json"',
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    # A hit that was then confirmed: retries_skipped.
    assert main(["--db", db, "--fmt", "json", "ask", "--err", err, "--eco", "py", "--rt", _py_rt()]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    capsys.readouterr()

    assert main(["--db", db, "--fmt", "json", "impact", "--offline"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["asks"] == 2 and out["hits"] == 1 and out["misses"] == 1
    assert out["retries_skipped"] == 1
    assert out["claims_published"] == 1
    assert out["misses_you_solved"] == 1
    assert out["replays_held"] == 1
    assert out["line"].startswith("# impact 7d: asks 2, hits 1, retries skipped 1")
    assert "public" not in out

    assert main(["--db", db, "impact", "--offline"]) == 0
    assert capsys.readouterr().out.startswith("# impact 7d:")


def test_impact_ledger_counts_only_your_claims(tmp_path: Path, monkeypatch):
    from claimidx.impact import ledger_impact
    from claimidx.models import Claim, EvalSpec, Fix
    from claimidx.fingerprint import fingerprint

    def _claim(own: str, nc: int, nr: int) -> Claim:
        err = f"RuntimeError: ledger probe {own}"
        return Claim(
            fp=fingerprint(err=err, eco="py"), cls="other", err=err, eco="py", fix=Fix(k="patch", b="x"), eval=EvalSpec(cmd="true"), own=own, nc=nc, nr=nr
        )

    rows = [_claim("did:claimidx:me", 3, 2), _claim("did:claimidx:me", 1, 0), _claim("did:claimidx:them", 9, 9)]
    monkeypatch.setattr("claimidx.home.fetch_ledger", lambda url=None: (rows, [], "test-ledger"))
    out = ledger_impact("did:claimidx:me")
    assert out["your_claims"] == 2 and out["confirmed_by_others"] == 4 and out["replayed_by_others"] == 2


def test_mcp_impact_tool(tmp_path: Path):
    from claimidx.mcp_server import _call
    from claimidx.store import Store

    out = _call("claimidx_impact", {"offline": True, "days": 30}, Store(tmp_path / "ix.sqlite"))
    assert out["days"] == 30 and out["asks"] == 0 and out["line"].startswith("# impact 30d")
