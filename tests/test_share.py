from pathlib import Path

from fastapi.testclient import TestClient

from claimidx.api import create_app
from claimidx.cli import main
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.home import already_shared, parse_ledger, share_claim
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def _local_claim(own: str = "did:claimidx:grok") -> Claim:
    err = "ModuleNotFoundError: No module named 'share_demo'"
    return Claim(
        fp=fingerprint(err=err, eco="py", rt="py@3.13", dep=["share-demo@1.0"]),
        cls="module_not_found",
        err=normalize_error(err),
        eco="py",
        rt="py@3.13",
        dep=["share-demo@1.0"],
        fix=Fix(k="pin", b="pip install share-demo"),
        eval=EvalSpec(cmd="true"),
        own=own,
        src="local",
    )


def test_share_without_api_writes_outbox(tmp_path: Path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(outbox))
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    store = Store(tmp_path / "agent.sqlite")
    c = store.put(_local_claim())
    result = share_claim(store, c)
    assert result["status"] == "outbox"
    assert outbox.exists()
    line = outbox.read_text(encoding="utf-8").strip()
    assert c.id in line
    assert already_shared(store, c.id)
    again = share_claim(store, c)
    assert again["status"] == "already"


def test_cli_share_and_second_agent_pull(tmp_path: Path, monkeypatch):
    """Agent A publishes + shares to a live home. Agent B pulls the ledger and asks."""
    home_db = tmp_path / "home.sqlite"
    agent_db = str(tmp_path / "agent.sqlite")
    peer_db = tmp_path / "peer.sqlite"
    app = create_app(str(home_db))
    client = TestClient(app)

    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:grok")
    rc = main([
        "--db", agent_db, "--fmt", "id", "publish",
        "--err", "ModuleNotFoundError: No module named 'federate_mod'",
        "--eco", "py", "--rt", "py@3.13",
        "--fix-k", "pin", "--fix-b", "pip install federate-mod", "--eval", "true",
    ])
    assert rc == 0

    # share by POSTing the same claim the CLI would send
    body = {
        "err": "ModuleNotFoundError: No module named 'federate_mod'",
        "fix_k": "pin",
        "fix_b": "pip install federate-mod",
        "eval": "true",
        "eco": "py",
        "rt": "py@3.13",
        "own": "did:claimidx:grok",
    }
    posted = client.post("/api/publish", json=body)
    assert posted.status_code == 200, posted.text
    payload = posted.json()
    assert payload["claim"]["own"] == "did:claimidx:grok"
    assert payload["claim"]["src"] == "home"
    assert payload["claim"]["st"] == "proposed"

    ledger = client.get("/ledger.jsonl")
    assert ledger.status_code == 200
    claims, skipped = parse_ledger(ledger.text)
    assert not skipped
    assert len(claims) == 1
    peer = Store(peer_db)
    for c in claims:
        c.src = "home"
        peer.put(c)
    stored = peer.all()
    assert stored[0].src == "home"
    assert stored[0].st == "proposed"

    rc = main([
        "--db", str(peer_db), "--fmt", "json", "ask",
        "--err", "ModuleNotFoundError: No module named 'federate_mod'",
        "--eco", "py",
    ])
    assert rc == 0


def test_home_api_refuses_anon(tmp_path: Path):
    app = create_app(str(tmp_path / "home.sqlite"))
    client = TestClient(app)
    r = client.post("/api/publish", json={
        "err": "x failed",
        "fix_k": "constraint",
        "fix_b": "do not retry blindly",
        "eval": "true",
        "own": "did:claimidx:anon",
    })
    assert r.status_code == 403


def test_home_token_required(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_HOME_TOKEN", "spt_testtoken")
    app = create_app(str(tmp_path / "home.sqlite"))
    client = TestClient(app)
    body = {
        "err": "x failed",
        "fix_k": "constraint",
        "fix_b": "do not retry blindly",
        "eval": "true",
        "own": "did:claimidx:harper",
    }
    denied = client.post("/api/publish", json=body)
    assert denied.status_code == 401
    ok = client.post("/api/publish", json=body, headers={"Authorization": "Bearer spt_testtoken"})
    assert ok.status_code == 200, ok.text


def test_publish_preserves_id_and_force(tmp_path: Path):
    app = create_app(str(tmp_path / "home.sqlite"))
    client = TestClient(app)
    body = {
        "id": "spr_bbbbbbbbbbbbbbbb",
        "err": "x failed twice",
        "fix_k": "constraint",
        "fix_b": "do not retry blindly",
        "eval": "true",
        "own": "did:claimidx:harper",
    }
    first = client.post("/api/publish", json=body)
    assert first.status_code == 200, first.text
    assert first.json()["claim"]["id"] == "spr_bbbbbbbbbbbbbbbb"
    body["fix_b"] = "still do not retry blindly"
    body["force"] = True
    second = client.post("/api/publish", json=body)
    assert second.status_code == 200, second.text
    assert second.json()["claim"]["id"] == "spr_bbbbbbbbbbbbbbbb"
    assert second.json()["claim"]["fix"]["b"].startswith("still")


def test_api_confirm_replay(tmp_path: Path):
    app = create_app(str(tmp_path / "home.sqlite"))
    client = TestClient(app)
    posted = client.post("/api/publish", json={
        "err": "x failed replay",
        "fix_k": "constraint",
        "fix_b": "ok",
        "eval": "python -c \"print(1)\"",
        "own": "did:claimidx:lucas",
        "rt": "py@3.13",
    })
    cid = posted.json()["claim"]["id"]
    denied = client.post(f"/api/claims/{cid}/confirm")
    assert denied.status_code == 409
    ok = client.post(f"/api/claims/{cid}/confirm?replay=true")
    assert ok.status_code == 200, ok.text
    assert ok.json()["held"] is True
    assert ok.json().get("recorded") is True
    assert "replay" not in ok.json()


def test_api_publish_refuses_id_clobber(tmp_path: Path):
    app = create_app(str(tmp_path / "home.sqlite"))
    client = TestClient(app)
    first = client.post("/api/publish", json={
        "err": "id clobber one",
        "fix_k": "constraint",
        "fix_b": "a",
        "eval": "true",
        "own": "did:claimidx:lucas",
        "id": "cix_aaaaaaaaaaaaaaaa",
    })
    assert first.status_code == 200, first.text
    clash = client.post("/api/publish", json={
        "err": "id clobber two",
        "fix_k": "constraint",
        "fix_b": "b",
        "eval": "true",
        "own": "did:claimidx:lucas",
        "id": "cix_aaaaaaaaaaaaaaaa",
    })
    assert clash.status_code == 409
    forced = client.post("/api/publish", json={
        "err": "id clobber two",
        "fix_k": "constraint",
        "fix_b": "b",
        "eval": "true",
        "own": "did:claimidx:lucas",
        "id": "cix_aaaaaaaaaaaaaaaa",
        "force": True,
    })
    assert forced.status_code == 200, forced.text
    assert forced.json()["claim"]["fix"]["b"] == "b"


def test_api_reject(tmp_path: Path):
    app = create_app(str(tmp_path / "home.sqlite"))
    client = TestClient(app)
    posted = client.post("/api/publish", json={
        "err": "x reject me",
        "fix_k": "constraint",
        "fix_b": "no",
        "eval": "true",
        "own": "did:claimidx:harper",
    })
    cid = posted.json()["claim"]["id"]
    r = client.post(f"/api/claims/{cid}/reject")
    assert r.status_code == 200, r.text
    assert r.json()["st"] == "rejected"
    ledger = client.get("/ledger.jsonl").text
    assert cid not in ledger


def test_true_replay_is_builtin():
    from claimidx.sandbox import replay

    held = replay("true", 0)
    assert held.held and held.reason == "builtin"
    missed = replay("false", 0)
    assert missed.ran and not missed.held
