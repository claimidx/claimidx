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


def test_commons_funnel_counts_and_first_share(tmp_path: Path):
    from datetime import UTC, datetime, timedelta

    from claimidx.impact import commons_funnel, render_line
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    a = "did:claimidx:alice"
    b = "did:claimidx:bob"
    c = "did:claimidx:carol"
    now = datetime.now(UTC)
    old = (now - timedelta(days=60)).isoformat()
    recent = (now - timedelta(days=2)).isoformat()
    with store._conn() as con:
        for claim_id, kind, actor, ts in (
            ("c1", "commons-push", a, old),  # alice's first push is outside the window
            ("c2", "commons-push", a, recent),
            ("c3", "commons-refused", a, recent),
            ("c4", "commons-skip", b, recent),
            ("c5", "commons-push", b, recent),  # bob's first push is in-window
            ("c6", "commons-hold", b, recent),
            ("c7", "commons-fail", c, recent),
            ("c8", "commons-push", c, recent),  # carol's first push is in-window
            ("c9", "ask", a, recent),  # ignored
        ):
            con.execute(
                "INSERT INTO events(claim_id, kind, actor, ts, detail) VALUES(?,?,?,?,?)",
                (claim_id, kind, actor, ts, None),
            )

    funnel = commons_funnel(store, days=30)
    assert funnel["pushes"] == {"count": 3, "actors": 3}
    assert funnel["refused"] == 1
    assert funnel["skipped"] == 1
    assert funnel["holds"] == 1
    assert funnel["fails"] == 1
    by = {row["actor"]: row for row in funnel["by_actor"]}
    assert by[a] == {"actor": a, "push": 1, "refused": 1, "skip": 0, "hold": 0, "fail": 0}
    assert by[b] == {"actor": b, "push": 1, "refused": 0, "skip": 1, "hold": 1, "fail": 0}
    assert by[c] == {"actor": c, "push": 1, "refused": 0, "skip": 0, "hold": 0, "fail": 1}
    assert funnel["first_share_actors"]["count"] == 2
    assert funnel["first_share_actors"]["actors"] == [b, c]

    line = render_line({"days": 30, "asks": 0, "hits": 0, "retries_skipped": 0, "claims_published": 0, "replays_held": 0, "funnel": funnel})
    assert "funnel: push 3 refuse 1 skip 1 first-share 2" in line


def test_impact_includes_funnel_when_commons_enabled(tmp_path: Path, monkeypatch):
    from claimidx.impact import impact
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(tmp_path / "ix.sqlite")
    store.log("commons-push", "did:claimidx:test", "c1")
    store.log("commons-skip", "did:claimidx:test", "c2")
    out = impact(store, days=30, own="did:claimidx:test", offline=True)
    assert out["funnel"]["pushes"]["count"] == 1
    assert out["funnel"]["skipped"] == 1
    assert out["funnel"]["first_share_actors"]["count"] == 1
    assert "funnel: push 1 refuse 0 skip 1 first-share 1" in out["line"]
