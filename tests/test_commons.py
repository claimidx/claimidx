"""The commons: sharing is the default path, opting out is the flag.

Transport is stubbed; nothing here reaches home.claimidx.com.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claimidx import home
from claimidx.cli import main
from claimidx.fingerprint import fingerprint
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def _claim(err: str = "ModuleNotFoundError: No module named 'tomli'", ev: str = 'python -c "import tomli"', own: str = "did:claimidx:agent-a") -> Claim:
    return Claim(
        fp=fingerprint(err=err, eco="py"), cls="module_not_found", err=err, eco="py", fix=Fix(k="pin", b="tomli==2.0.1"), eval=EvalSpec(cmd=ev), own=own
    )


@pytest.fixture
def commons(monkeypatch):
    """Commons on, transport captured."""
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    calls: list[tuple[str, dict]] = []

    def fake_post(url, payload, token="", timeout=20.0):
        calls.append((url, payload))
        return {"exists": False, "claim": {"id": payload.get("id")}}

    monkeypatch.setattr(home, "_post", fake_post)
    return calls


def test_share_goes_to_the_commons_without_any_home_or_token(tmp_path: Path, commons):
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())
    out = home.share_claim(store, c)
    assert out["status"] == "commons", out
    url, payload = commons[0]
    assert url == home.COMMONS_API + "/api/publish"
    assert payload["id"] == c.id and payload["own"] == "did:claimidx:agent-a" and payload["fix_b"] == "tomli==2.0.1"
    assert payload["eval"]["cmd"] == 'python -c "import tomli"' and "note" not in payload or not payload.get("note")
    assert home.commons_shared(store, c.id)
    # Idempotent.
    assert home.share_claim(store, c)["status"] == "already" and len(commons) == 1


def test_unreachable_commons_queues_and_sync_flushes(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())

    def down(url, payload, token="", timeout=20.0):
        raise home.HomeError("connection refused")

    monkeypatch.setattr(home, "_post", down)
    out = home.share_claim(store, c)
    assert out["status"] == "outbox" and "sync" in out["hint"], out
    outbox = Path(out["path"])
    assert outbox.exists() and json.loads(outbox.read_text(encoding="utf-8").splitlines()[0])["id"] == c.id
    assert not home.commons_shared(store, c.id)
    sent: list[dict] = []
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: sent.append(payload) or {"exists": False})
    flushed = home.flush_outbox(store)
    assert flushed == {"sent": 1, "kept": 0} and not outbox.exists() and sent[0]["id"] == c.id
    assert home.commons_shared(store, c.id)


def test_hint_evals_never_reach_the_commons(tmp_path: Path, commons):
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim(ev="true"))
    out = home.share_claim(store, c)
    assert out["commons"]["status"] == "skipped" and not commons


def test_commons_off_keeps_the_old_outbox_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())
    monkeypatch.setattr(home, "_post", lambda *a, **k: pytest.fail("must not post"))
    out = home.share_claim(store, c)
    assert out["status"] == "outbox" and "commons" not in out


def test_private_home_and_commons_both_receive_a_claim(tmp_path: Path, commons, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_HOME_API", "http://private.example/t/acme")
    monkeypatch.setenv("CLAIMIDX_HOME_TOKEN", "spt_x")
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())
    out = home.share_claim(store, c)
    assert out["status"] == "pushed" and out["commons"]["status"] == "commons"
    urls = [u for u, _ in commons]
    assert urls == ["http://private.example/t/acme/api/publish", home.COMMONS_API + "/api/publish"]


def test_pull_defaults_to_the_commons_and_falls_back_to_the_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    assert home.ledger_url() == home.COMMONS_LEDGER
    seen: list[str] = []

    def fake_get(url, timeout=20.0):
        seen.append(url)
        if url == home.COMMONS_LEDGER:
            raise home.HomeError("503")
        return (_claim().model_dump_json() + "\n").encode("utf-8")

    monkeypatch.setattr(home, "_get", fake_get)
    claims, skipped, target = home.fetch_ledger()
    assert target == home.DEFAULT_LEDGER and len(claims) == 1 and seen == [home.COMMONS_LEDGER, home.DEFAULT_LEDGER]
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    assert home.ledger_url() == home.DEFAULT_LEDGER


def test_claim_yes_shares_to_the_commons_and_local_opts_out(tmp_path: Path, commons, capsys):
    from claimidx.env import remember_failure

    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--no-clean-room", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["share"]["status"] == "commons"
    # The publish, then the held replay reported as a confirm on the commons.
    assert [u.split("/api/")[1].split("?")[0] for u, _ in commons] == ["publish", f"claims/{out['id']}/confirm"]
    remember_failure("ModuleNotFoundError: No module named 'csv'", cwd=str(tree), eco="py")
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--no-clean-room", "--local", "--fix", "pip install csv"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert not out.get("share") and len(commons) == 2


def test_scratch_uses_a_throwaway_index_and_never_shares(tmp_path: Path, commons, capsys, monkeypatch):
    from claimidx.env import remember_failure

    monkeypatch.setenv("CLAIMIDX_SCRATCH_DIR", str(tmp_path / "claimidx-scratch"))
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    assert main(["--scratch", "--fmt", "json", "claim", "--yes", "--no-diff", "--no-clean-room", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["id"] and not out.get("share") and not commons
    assert (tmp_path / "claimidx-scratch" / "index.sqlite").exists()
    assert not (tmp_path / "ix.sqlite").exists()


def test_hooks_nudge_when_replayable_claims_are_unshared(tmp_path: Path, monkeypatch, commons):
    from claimidx.hook import session_brief, stop_reminder, unshared_claims

    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())
    store.put(_claim(err="RuntimeError: only a hint", ev="true"))
    assert unshared_claims(store) == [c.id]
    assert "1 replayable claim live" in session_brief(store)
    rem = stop_reminder(store)
    assert rem and "claimidx sync" in rem["hookSpecificOutput"]["additionalContext"]
    assert stop_reminder(store) is None  # once per window
    home.share_claim(store, c)
    assert unshared_claims(store) == [] and "replayable claim" not in session_brief(store)


def test_verdict_calls_a_hint_a_hint(tmp_path: Path, capsys):
    """A claim whose eval cannot be replayed is a note; the verdict must not say `apply`."""
    from claimidx.match import verdict_for

    hint = _claim(ev="true")
    hint.fix = Fix(k="cmd", b="run the thing")
    v = verdict_for({"err": hint.err, "cls": hint.cls, "eco": "py", "rt": "", "dep": [], "fp": hint.fp}, [(hint, 1.0)])
    assert v["action"] == "hint" and "not a proof" in v["next"] and "claim --yes" in v["next"]
    proof = _claim()
    v = verdict_for({"err": proof.err, "cls": proof.cls, "eco": "py", "rt": "", "dep": [], "fp": proof.fp}, [(proof, 1.0)])
    assert v["action"] == "apply"
    cmd = _claim()
    cmd.fix = Fix(k="cmd", b="rm -rf node_modules && npm i")
    v = verdict_for({"err": cmd.err, "cls": cmd.cls, "eco": "py", "rt": "", "dep": [], "fp": cmd.fp}, [(cmd, 1.0)])
    assert v["action"] == "review"
