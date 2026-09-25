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


def test_lifecycle_funnel_stages_and_dropoff(tmp_path: Path):
    from claimidx.impact import lifecycle_funnel, render_line
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    a = "did:claimidx:alice"
    b = "did:claimidx:bob"
    store.log("init", a, "", {"stage": "init"})
    store.log("install", a, "", {"stage": "install", "surfaces": ["cursor"]})
    store.log("ask", a, "c1", {"hit": False, "n": 0})
    store.log("init", b, "", {"stage": "init"})  # bob stops at init
    store.log("init", "did:claimidx:anon", "", {"stage": "init"})  # excluded from countable

    life = lifecycle_funnel(store, days=30)
    assert life["order"][0] == "install"
    assert life["stages"]["init"]["actors"] == 2
    assert life["stages"]["ask"]["actors"] == 1
    assert life["stages"]["install"]["actors"] == 1
    assert life["countable_actors"] == 2
    assert life["dropoff"]["init_no_ask"] == 1  # bob
    assert "did:claimidx:anon" not in life["countable_actor_ids"]

    line = render_line({"days": 30, "asks": 0, "hits": 0, "retries_skipped": 0, "claims_published": 0, "replays_held": 0, "lifecycle": life})
    assert "lifecycle:" in line and "countable 2" in line


def test_init_emits_funnel_stages(tmp_path: Path, capsys, monkeypatch):
    import json

    from claimidx.cli import main
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "funnel-a", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    kinds = {e["kind"] for e in Store(db).events(limit=20)}
    assert "init" in kinds
    assert "install" not in kinds  # --no-hooks

    assert main(["--db", db, "init", "--agent", "funnel-a", "--offline"]) == 0
    capsys.readouterr()
    events = Store(db).events(limit=50)
    kinds = {e["kind"] for e in events}
    assert "install" in kinds
    init_rows = [e for e in events if e["kind"] == "init"]
    assert init_rows and init_rows[0]["actor"] == "did:claimidx:funnel-a"
    assert init_rows[0]["detail"].get("stage") == "init"

    assert main(["--db", db, "--fmt", "json", "impact", "--offline", "--days", "30"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lifecycle"]["stages"]["init"]["actors"] >= 1
    assert out["lifecycle"]["countable_actors"] >= 1

    assert main(["--db", db, "doctor"]) in (0, 2)
    doc = json.loads(capsys.readouterr().out)
    assert "funnel" in doc and doc["funnel"]["stages"]["init"]["actors"] >= 1
    assert any(c["name"] == "funnel" for c in doc["checks"])


def test_api_funnel_endpoint(tmp_path: Path):
    from fastapi.testclient import TestClient

    from claimidx.api import create_app
    from claimidx.store import Store

    db = tmp_path / "ix.sqlite"
    Store(db).log("init", "did:claimidx:growth", "", {"stage": "init"})
    client = TestClient(create_app(str(db)))
    res = client.get("/api/funnel?days=30")
    assert res.status_code == 200
    body = res.json()
    assert body["lifecycle"]["stages"]["init"]["actors"] == 1
    assert body["lifecycle"]["countable_actors"] == 1


def test_funnel_cli_scoreboard(tmp_path: Path, capsys):
    from claimidx.cli import main
    from claimidx.store import Store

    db = str(tmp_path / "ix.sqlite")
    store = Store(db)
    a = "did:claimidx:alice"
    b = "did:claimidx:bob"
    store.log("install", a, "", {"stage": "install"})
    store.log("init", a, "", {"stage": "init"})
    store.log("ask", a, "c1", {"hit": False})
    store.log("confirm-replay", a, "c1", {"held": True})
    store.log("publish", a, "c1", {})
    store.log("init", b, "", {"stage": "init"})
    store.log("init", "did:claimidx:anon", "", {"stage": "init"})

    assert main(["--db", db, "funnel", "--days", "30"]) == 0
    out = capsys.readouterr().out
    assert "local home event log only" in out
    assert "countable DIDs (excl. seed/anon): 2" in out
    assert "COO path actors:" in out and "hold 1" in out and "claim 1" in out
    assert "init_no_ask" in out

    assert main(["--db", db, "--fmt", "json", "funnel", "--days", "30"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["lifecycle"]["countable_actors"] == 2
    assert payload["lifecycle"]["stages"]["ask"]["actors"] == 1
    assert "local home" in payload["locality"]
    assert payload["db"].endswith("ix.sqlite")


def test_render_funnel_scoreboard_aliases():
    from claimidx.impact import render_funnel_scoreboard

    life = {
        "days": 7,
        "order": ["install", "init", "ask", "sync", "confirm", "publish", "share"],
        "stages": {
            "install": {"actors": 1, "events": 1},
            "init": {"actors": 2, "events": 2},
            "ask": {"actors": 1, "events": 1},
            "sync": {"actors": 0, "events": 0},
            "confirm": {"actors": 1, "events": 1},
            "publish": {"actors": 1, "events": 1},
            "share": {"actors": 0, "events": 0},
        },
        "countable_actors": 2,
        "dropoff": {"init_no_ask": 1, "install_no_init": 0, "ask_no_sync": 0, "sync_no_confirm": 0, "confirm_no_publish": 0, "publish_no_share": 1},
    }
    text = render_funnel_scoreboard(life)
    assert "confirm (≈hold)" in text
    assert "publish (≈claim)" in text
    assert "COO path actors: install 1 → init 2 → ask 1 → hold 1 → claim 1" in text
