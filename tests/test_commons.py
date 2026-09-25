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
    assert flushed == {"sent": 1, "kept": 0, "refused": 0, "unreachable": False} and not outbox.exists() and sent[0]["id"] == c.id
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
    assert out["share"]["status"] == "local" and len(commons) == 2


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


def test_hooks_share_replayable_claims_themselves_and_leave_hints_alone(tmp_path: Path, monkeypatch, commons):
    """An unshared replayable claim is the hooks' job, not a chore for the agent; a hint eval is never a backlog."""
    from claimidx.hook import session_brief, stop_reminder, unshared_claims

    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())
    hint = store.put(_claim(err="RuntimeError: only a hint", ev="true"))
    assert unshared_claims(store) == [c.id]
    assert "Shared 1 claim to the commons." in session_brief(store)
    assert [pl["id"] for _u, pl in commons] == [c.id]
    assert home.commons_shared(store, c.id) and not home.commons_shared(store, hint.id)
    assert unshared_claims(store) == [] and "claim" not in session_brief(store).split("claim --yes`.")[-1]
    assert stop_reminder(store) is None  # nothing owed, nothing said


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


def test_a_private_home_refusal_does_not_keep_the_claim_off_the_commons(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.setenv("CLAIMIDX_HOME_API", "http://private.example/t/acme")
    store = Store(str(tmp_path / "ix.sqlite"))
    a = store.put(_claim())
    b = store.put(_claim(err="ModuleNotFoundError: No module named 'tomllib'"))
    posted: list[str] = []

    def fake_post(url, payload, token="", timeout=20.0):
        if url.startswith("http://private.example"):
            raise home.HomeError("home POST 400: eval head not allowlisted")
        posted.append(payload.get("id"))
        return {"exists": False}

    monkeypatch.setattr(home, "_post", fake_post)
    out = home.share_claim(store, a)
    assert out["status"] == "commons" and out["home"]["status"] == "error"
    pending = home.share_pending(store)
    assert pending["n"] == 1 and posted == [a.id, b.id]  # the run went on past the refusal


def test_local_is_a_durable_decision_not_a_flag_for_one_run(tmp_path: Path, commons, capsys, monkeypatch):
    """Record with --local, come back later, sync: the claim stays on this machine until someone shares it by id."""
    from claimidx.env import remember_failure
    from claimidx.hook import unshared_claims

    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--no-clean-room", "--local", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    cid = out["id"]
    assert out["share"] == {"status": "local", "id": cid, "hint": f"kept on this machine; `claimidx share {cid}` publishes it"}
    assert not commons
    # A new process, a reconnect, a bulk share: still local.
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    store = Store(db)
    assert home.keep_local(store, cid)
    assert unshared_claims(store) == []
    assert main(["--db", db, "--fmt", "json", "sync", "--no-pull"]) == 0
    assert not commons
    assert main(["--db", db, "--fmt", "json", "share"]) == 0
    assert not commons
    # A replay of the claim later does not leak it either.
    assert home.share_observation(store, store.get(cid), held=True, actor="did:claimidx:test") is None
    # The separate publication decision: share by id.
    assert main(["--db", db, "--fmt", "json", "share", cid]) == 0
    assert [u for u, _ in commons] == [home.COMMONS_API + "/api/publish"]
    assert not home.keep_local(store, cid)


def test_success_output_names_the_destination(tmp_path: Path, commons, capsys, monkeypatch):
    from claimidx.env import remember_failure

    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    assert main(["--db", db, "claim", "--yes", "--no-diff", "--no-clean-room", "--fix", "pip install json"]) == 0
    err = capsys.readouterr().err
    assert "shared: commons" in err
    remember_failure("ModuleNotFoundError: No module named 'csv'", cwd=str(tree), eco="py")
    assert main(["--db", db, "claim", "--yes", "--no-diff", "--no-clean-room", "--local", "--fix", "pip install csv"]) == 0
    err = capsys.readouterr().err
    assert "kept on this machine" in err and "claimidx share cix_" in err

    def down(url, payload, token="", timeout=20.0):
        raise home.HomeError("connection refused")

    monkeypatch.setattr(home, "_post", down)
    remember_failure("ModuleNotFoundError: No module named 'abc'", cwd=str(tree), eco="py")
    assert main(["--db", db, "claim", "--yes", "--no-diff", "--no-clean-room", "--fix", "pip install abc"]) == 0
    err = capsys.readouterr().err
    assert "queued" in err and "claimidx sync" in err and "not private" in err


def test_publish_local_is_durable_across_sync(tmp_path: Path, commons, capsys, monkeypatch):
    """Path B CLI (publish/ingest --local): sync must not auto-publish; share <id> is the decision."""
    from claimidx.hook import unshared_claims

    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "json",
                "publish",
                "--local",
                "--err",
                "ModuleNotFoundError: No module named 'localpriv'",
                "--eco",
                "py",
                "--fix-k",
                "constraint",
                "--fix-b",
                "document local privacy",
                "--eval",
                'python -c "import json"',
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    cid = out["id"]
    assert out["share"]["status"] == "local"
    assert not commons
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    store = Store(db)
    assert home.keep_local(store, cid)
    assert unshared_claims(store) == []
    assert main(["--db", db, "--fmt", "json", "sync", "--no-pull"]) == 0
    assert not commons
    assert main(["--db", db, "--fmt", "json", "share"]) == 0
    assert not commons
    assert main(["--db", db, "--fmt", "json", "share", cid]) == 0
    assert [u for u, _ in commons] == [home.COMMONS_API + "/api/publish"]
    assert not home.keep_local(store, cid)


def test_publish_success_output_names_the_destination(tmp_path: Path, commons, capsys, monkeypatch):
    """Human publish/ingest success must name destination the same way claim --yes does (B2 Path B)."""
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'destpub'",
                "--eco",
                "py",
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                'python -c "import json"',
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "shared: commons" in err

    assert (
        main(
            [
                "--db",
                db,
                "publish",
                "--local",
                "--err",
                "ModuleNotFoundError: No module named 'destlocal'",
                "--eco",
                "py",
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                'python -c "import json"',
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "kept on this machine" in err and "claimidx share cix_" in err

    def down(url, payload, token="", timeout=20.0):
        raise home.HomeError("connection refused")

    monkeypatch.setattr(home, "_post", down)
    assert (
        main(
            [
                "--db",
                db,
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'destqueue'",
                "--eco",
                "py",
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                'python -c "import json"',
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "queued" in err and "claimidx sync" in err and "not private" in err
    assert "outbox" in err.lower() or str(tmp_path) in err or ".jsonl" in err


def test_mcp_local_is_durable_across_sync(tmp_path: Path, commons, monkeypatch):
    """Path B MCP local=true must survive sync the same way CLI --local does."""
    from claimidx.hook import unshared_claims
    from claimidx.mcp_server import _call

    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:path-b-local")
    store = Store(tmp_path / "ix.sqlite")
    pub = _call(
        "claimidx_ingest",
        {
            "err": "ModuleNotFoundError: No module named 'mcplocal'",
            "eco": "py",
            "fix_k": "constraint",
            "fix_b": "ok",
            "eval": 'python -c "import json"',
            "local": True,
        },
        store,
    )
    cid = pub["id"]
    assert pub["share"]["status"] == "local"
    assert not commons
    assert home.keep_local(store, cid)
    assert unshared_claims(store) == []
    sync = _call("claimidx_sync", {"no_pull": True}, store)
    assert not commons
    assert sync.get("share", {}).get("n", 0) == 0
    shared = _call("claimidx_share", {"id": cid}, store)
    assert shared.get("status") in {"commons", "pushed"} or (shared.get("commons") or {}).get("status") == "commons"
    assert [u for u, _ in commons] == [home.COMMONS_API + "/api/publish"]
    assert not home.keep_local(store, cid)


def test_a_projection_without_a_replayable_eval_stays_local_without_queueing(tmp_path: Path, monkeypatch):
    """A tree-specific recipe projects to an empty eval; the commons would refuse it, so it is skipped, not queued forever."""
    from claimidx.hook import unshared_claims

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(str(tmp_path / "ix.sqlite"))
    err = "AssertionError: recipe file missing an entry"
    c = store.put(
        Claim(
            fp=fingerprint(err=err, eco="py"),
            cls="other",
            err=err,
            eco="py",
            fix=Fix(k="patch", b="diff --git a/x b/x"),
            eval=EvalSpec(cmd="python -c \"from pathlib import Path; assert 'x' in Path(r'C:/pack/recipes.js').read_text()\""),
            own="did:claimidx:agent-a",
        )
    )
    monkeypatch.setattr(home, "_post", lambda *a, **k: pytest.fail("must not post a projection the commons refuses"))
    out = home.share_claim(store, c)
    assert out["commons"]["status"] == "skipped" and "replayable" in out["commons"]["reason"], out
    assert not Path(home.outbox_path()).exists()
    assert unshared_claims(store) == []  # nothing to nudge about
    assert home.share_pending(store)["n"] == 0


def test_a_commons_refusal_is_recorded_not_queued_but_an_outage_is(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())

    def refuse(url, payload, token="", timeout=20.0):
        raise home.HomeError('home POST 400: {"error":"eval is a hint; the commons keeps claims that can be replayed"}')

    monkeypatch.setattr(home, "_post", refuse)
    out = home.share_claim(store, c)
    assert out["commons"]["status"] == "refused" and "400" in out["commons"]["reason"], out
    assert not Path(home.outbox_path()).exists()
    from claimidx.hook import unshared_claims

    assert unshared_claims(store) == []
    d = store.put(_claim(err="ModuleNotFoundError: No module named 'tomllib'"))
    monkeypatch.setattr(home, "_post", lambda *a, **k: (_ for _ in ()).throw(home.HomeError("connection refused")))
    assert home.share_claim(store, d)["commons"]["status"] == "outbox"
    assert unshared_claims(store) == [d.id]


def test_flush_outbox_drops_refused_lines_and_keeps_transport_failures(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(str(tmp_path / "ix.sqlite"))
    outbox = Path(home.outbox_path())
    outbox.parent.mkdir(parents=True, exist_ok=True)
    outbox.write_text(
        json.dumps({"id": "cix_00000000000000a1", "own": "did:claimidx:x"}) + "\n" + json.dumps({"id": "cix_00000000000000b2", "own": "did:claimidx:x"}) + "\n",
        encoding="utf-8",
    )

    def post(url, payload, token="", timeout=20.0):
        if payload["id"].endswith("a1"):
            raise home.HomeError("home POST 400: refused")
        raise home.HomeError("connection refused")

    monkeypatch.setattr(home, "_post", post)
    assert home.flush_outbox(store) == {"sent": 0, "kept": 1, "refused": 1, "unreachable": False}
    assert [json.loads(ln)["id"] for ln in outbox.read_text(encoding="utf-8").splitlines() if ln.strip()] == ["cix_00000000000000b2"]


def test_is_refusal_splits_policy_from_transient_4xx():
    assert home._is_refusal("home POST 400: eval is a hint")
    assert home._is_refusal('home POST 422: {"error":"anonymous"}')
    assert home._is_refusal("home POST 409: refused")
    assert not home._is_refusal("home POST 429: rate limited")
    assert not home._is_refusal('home POST 429: {"Retry-After":30}')
    assert not home._is_refusal("home POST 408: request timeout")
    assert not home._is_refusal("home POST 401: unauthorized")
    assert not home._is_refusal("home POST 425: too early")
    # bare proxy/WAF statuses are transport until the body looks like a row judgment
    assert not home._is_refusal("home POST 403: Forbidden")
    assert not home._is_refusal("home POST 404: not found")
    assert home._is_refusal('home POST 403: {"error":"eval is a hint; the commons keeps claims that can be replayed"}')
    assert not home._is_refusal("connection refused")
    assert not home._is_refusal("home unreachable: timed out")


def test_transient_4xx_stays_in_outbox(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(str(tmp_path / "ix.sqlite"))
    c = store.put(_claim())

    def rate_limit(url, payload, token="", timeout=20.0):
        raise home.HomeError('home POST 429: {"Retry-After":60}')

    monkeypatch.setattr(home, "_post", rate_limit)
    out = home.share_claim(store, c)
    assert out["commons"]["status"] == "outbox", out
    assert Path(home.outbox_path()).is_file()
    from claimidx.hook import unshared_claims

    assert unshared_claims(store) == [c.id]


def test_session_start_sends_the_backlog_instead_of_asking(tmp_path: Path, monkeypatch, commons):
    """A queued projection and an unshared claim go out on SessionStart; the brief reports it, never `claimidx sync`."""
    from claimidx.hook import session_brief, unshared_claims

    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(tmp_path / "outbox.jsonl"))
    store = Store(str(tmp_path / "ix.sqlite"))
    queued = store.put(_claim(err="ModuleNotFoundError: No module named 'tomllib'"))
    monkeypatch.setattr(home, "_post", lambda *a, **k: (_ for _ in ()).throw(home.HomeError("connection refused")))
    assert home.share_claim(store, queued)["commons"]["status"] == "outbox"
    posted: list[str] = []
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: posted.append(payload["id"]) or {"exists": False})
    fresh = store.put(_claim())
    assert unshared_claims(store) == [queued.id, fresh.id]
    brief = session_brief(store)
    assert "Shared 2 claims to the commons" in brief and "claimidx sync" not in brief, brief
    assert posted == [queued.id, fresh.id]
    assert not Path(home.outbox_path()).exists()
    assert home.commons_shared(store, queued.id) and home.commons_shared(store, fresh.id)
    assert unshared_claims(store) == []
    assert "Shared" not in session_brief(store)  # nothing left: no line at all


def test_session_start_with_the_commons_down_tries_once_and_says_queued(tmp_path: Path, monkeypatch):
    from claimidx.hook import session_brief, stop_reminder

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(tmp_path / "outbox.jsonl"))
    store = Store(str(tmp_path / "ix.sqlite"))
    for name in ("tomli", "tomllib", "yaml"):
        store.put(_claim(err=f"ModuleNotFoundError: No module named '{name}'"))
    tries: list[str] = []

    def down(url, payload, token="", timeout=20.0):
        tries.append(payload["id"])
        raise home.HomeError("home unreachable: timed out")

    monkeypatch.setattr(home, "_post", down)
    brief = session_brief(store)
    assert "3 replayable claims queued" in brief and "unreachable" in brief and "next session" in brief, brief
    assert len(tries) == 1  # one probe, not one per claim
    # Stop tries again at most once per window, then stays quiet.
    rem = stop_reminder(store)
    assert rem and "queued" in rem["hookSpecificOutput"]["additionalContext"]
    assert len(tries) == 2
    assert stop_reminder(store) is None


def test_python_ingest_shares_by_default_and_share_false_keeps_it(tmp_path: Path, monkeypatch, commons):
    from claimidx import ingest

    db = str(tmp_path / "ix.sqlite")
    out = ingest(
        "ModuleNotFoundError: No module named 'tomli'",
        fix_k="pin",
        fix_b="tomli==2.0.1",
        eval='python -c "import tomli"',
        eco="py",
        own="did:claimidx:agent-a",
        db=db,
    )
    assert out["share"]["status"] == "commons", out
    assert [p["id"] for _u, p in commons] == [out["id"]]
    kept = ingest(
        "ModuleNotFoundError: No module named 'yaml'",
        fix_k="pin",
        fix_b="pyyaml==6.0",
        eval='python -c "import yaml"',
        eco="py",
        own="did:claimidx:agent-a",
        db=db,
        share=False,
    )
    assert kept["share"]["status"] == "local" and "claimidx share" in kept["share"]["hint"] and len(commons) == 1
    from claimidx.hook import unshared_claims

    assert unshared_claims(Store(db)) == []  # share=False is --local: a decision, not a backlog


def test_a_new_publish_drains_the_outbox_first(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(tmp_path / "outbox.jsonl"))
    store = Store(str(tmp_path / "ix.sqlite"))
    queued = store.put(_claim(err="ModuleNotFoundError: No module named 'tomllib'"))
    monkeypatch.setattr(home, "_post", lambda *a, **k: (_ for _ in ()).throw(home.HomeError("connection refused")))
    assert home.share_claim(store, queued)["status"] == "outbox"
    posted: list[str] = []
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: posted.append(payload["id"]) or {"exists": False})
    fresh = store.put(_claim())
    out = home.maybe_share(store, fresh)
    assert out and out["status"] == "commons" and out["outbox"]["sent"] == 1, out
    assert posted == [queued.id, fresh.id]
    assert not Path(home.outbox_path()).exists()
