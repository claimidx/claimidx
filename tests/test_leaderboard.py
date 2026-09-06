"""Signed holds reach the commons; the leaderboard reads back; impact and doctor see it."""

from __future__ import annotations

import json
from pathlib import Path

from claimidx import home
from claimidx.board import fetch_leaderboard, render_board, signed_observation
from claimidx.cli import main
from claimidx.fingerprint import fingerprint
from claimidx.identity import verify_record
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def _claim(own: str = "did:claimidx:agent-a") -> Claim:
    err = "ModuleNotFoundError: No module named 'tomli'"
    return Claim(
        fp=fingerprint(err=err, eco="py"),
        cls="module_not_found",
        err=err,
        eco="py",
        fix=Fix(k="pin", b="tomli==2.0.1"),
        eval=EvalSpec(cmd='python -c "import tomli"'),
        own=own,
    )


def test_signed_observation_verifies_and_provisions_a_key(tmp_path: Path):
    from claimidx.identity import local_key_path

    assert not local_key_path().exists()
    rec = signed_observation("cix_00000000000000a1", held=True, replayed=True, own="did:claimidx:agent-b")
    assert local_key_path().exists()
    assert rec["kind"] == "hold" and rec["claim_id"] == "cix_00000000000000a1" and rec["own"] == "did:claimidx:agent-b"
    assert rec["key_id"].startswith("did:key:z") and rec["signature"] and rec["ts"].endswith("Z")
    assert verify_record(rec)
    tampered = dict(rec, own="did:claimidx:someone-else")
    assert not verify_record(tampered)
    assert signed_observation("cix_00000000000000a1", held=True, replayed=False, own="x")["kind"] == "confirm"
    assert signed_observation("cix_00000000000000a1", held=False, replayed=True, own="x")["kind"] == "fail"


def test_a_replayed_hold_is_reported_to_the_private_home_and_signed_to_the_commons(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.setenv("CLAIMIDX_HOME_API", "http://private.example/t/acme")
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())
    posts: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        home, "_post", lambda url, payload, token="", timeout=20.0: posts.append((url, payload)) or {"claim": {"nc": 1, "nr": 1, "st": "confirmed"}}
    )
    home.share_claim(store, c)
    posts.clear()
    out = home.share_observation(store, c, held=True, actor="did:claimidx:agent-b")
    assert out and out["status"] == "confirm" and out["home"]["nr"] == 1 and out["commons"]["nr"] == 1
    urls = [u for u, _ in posts]
    assert urls[0] == f"http://private.example/t/acme/api/claims/{c.id}/confirm?own=did%3Aclaimidx%3Aagent-b&replay=true"
    assert urls[1] == f"{home.COMMONS_API}/api/claims/{c.id}/confirm"
    body = posts[1][1]
    assert body["kind"] == "hold" and body["own"] == "did:claimidx:agent-b" and verify_record(body)
    kinds = {e.get("kind") for e in store.events(limit=50)}
    assert "commons-hold" in kinds and "home-confirm" in kinds


def test_without_a_private_home_the_commons_still_gets_the_hold(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())
    posts: list[str] = []
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: posts.append(url) or {"claim": {}})
    home.share_claim(store, c)
    out = home.share_observation(store, c, held=False, actor="did:claimidx:agent-b")
    assert out["status"] == "fail" and "home" not in out and "commons" in out
    assert posts[-1].endswith(f"/api/claims/{c.id}/fail")
    # A local claim that never reached the commons has nothing to report there.
    d = store.put(_claim(own="did:claimidx:agent-c"))
    assert home.share_observation(store, d, held=True, actor="did:claimidx:agent-b") is None
    # A claim pulled from the commons is reported back to it: that is how another agent's claim earns a hold.
    e = _claim(own="did:claimidx:agent-d")
    e.src = "home"
    e = store.put(e)
    out = home.share_observation(store, e, held=True, actor="did:claimidx:agent-b")
    assert out and out["commons"] is not None and posts[-1].endswith(f"/api/claims/{e.id}/confirm")


BOARD = {
    "days": 30,
    "rules": ["only replayed holds (kind hold) count"],
    "authors": [
        {"rank": 1, "own": "did:claimidx:grok", "holds": 4, "verifiers": 3, "claims_held": 2, "claims": 10, "first_seen": "2026-08-01T00:00:00.000Z"},
        {"rank": 2, "own": "did:claimidx:agent-a", "holds": 1, "verifiers": 1, "claims_held": 1, "claims": 1, "first_seen": "2026-09-01T00:00:00.000Z"},
    ],
    "verifiers": [{"rank": 1, "own": "did:claimidx:agent-b", "holds": 5, "claims": 3, "authors": 2, "first_seen": "2026-09-02T00:00:00.000Z"}],
    "you": {"author": {"rank": 2, "own": "did:claimidx:agent-a", "holds": 1, "verifiers": 1}, "verifier": None},
}


def test_leaderboard_command_and_impact_read_the_commons(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:agent-a")
    seen: list[str] = []

    def fake_get(url, timeout=20.0):
        seen.append(url)
        if "/api/leaderboard" in url:
            return json.dumps(BOARD).encode("utf-8")
        raise home.HomeError("no ledger in this test")

    monkeypatch.setattr(home, "_get", fake_get)
    board = fetch_leaderboard(days=7, limit=10, own="did:claimidx:agent-a")
    assert seen[-1] == home.COMMONS_API + "/api/leaderboard?days=7&limit=10&own=did%3Aclaimidx%3Aagent-a"
    text = render_board(board, own="did:claimidx:agent-a")
    assert "did:claimidx:grok  holds 4  verifiers 3" in text and "<- you" in text and "verifiers:" in text
    assert main(["--db", str(tmp_path / "ix.sqlite"), "leaderboard", "--days", "7"]) == 0
    assert "<- you" in capsys.readouterr().out
    assert main(["--db", str(tmp_path / "ix.sqlite"), "--fmt", "json", "impact"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["commons"]["held_by_others"] == 1 and out["commons"]["rank"] == 2 and out["commons"]["you_held"] == 0
    assert "commons 30d: held by others 1 (1 verifiers, rank 2), you held 0" in out["line"]


def test_render_board_when_nothing_is_held_says_what_to_do():
    text = render_board({"days": 30, "authors": [], "verifiers": []}, own="did:claimidx:agent-a")
    assert "nothing held yet" in text and "not on the board yet" in text


def test_doctor_reports_the_commons(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.setattr(
        home,
        "_get",
        lambda url, timeout=20.0: (
            json.dumps({"ok": True, "claims": 5}).encode("utf-8") if url.endswith("/api/health") else (_ for _ in ()).throw(home.HomeError("x"))
        ),
    )
    main(["--db", str(tmp_path / "ix.sqlite"), "--fmt", "json", "doctor"])  # exit code reflects other checks
    out = capsys.readouterr().out
    assert "commons" in out and "claims=5" in out
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    main(["--db", str(tmp_path / "ix.sqlite"), "--fmt", "json", "doctor"])
    assert "nothing leaves this machine" in capsys.readouterr().out


def test_render_board_shows_eval_classes():
    board = {
        "days": 30,
        "authors": [
            {
                "rank": 1,
                "own": "did:claimidx:grok",
                "holds": 3,
                "verifiers": 2,
                "claims_held": 3,
                "claims": 5,
                "by_eval": {"presence": 1, "version": 1, "recipe": 1},
            }
        ],
        "verifiers": [],
    }
    text = render_board(board)
    assert "recipe 1" in text and "version 1" in text and "presence 1" in text
