"""0.7.21 batch: live-ledger funnel, board alias, scratch identity, inspector links, path_b id, MCP args, replay held/recorded."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from claimidx import home, sandbox
from claimidx.cli import main
from claimidx.fingerprint import fingerprint
from claimidx.store import Store


def _row(i: int, own: str) -> dict:
    err = f"ModuleNotFoundError: No module named m{i}"
    return {
        "v": 1,
        "id": f"cix_0721{i:012x}",
        "fp": fingerprint(err=err, cls="module_not_found", eco="py", rt="py@3.12", dep=[]),
        "cls": "module_not_found",
        "err": err,
        "eco": "py",
        "rt": "py@3.12",
        "dep": [],
        "tool": [],
        "tried": [],
        "fix": {"k": "pin", "b": f"pip install m{i}"},
        "eval": {"cmd": f'python -c "import m{i}"', "expect": 0},
        "st": "confirmed",
        "nc": 1,
        "nf": 0,
        "own": own,
        "model": "",
        "ts": "2026-09-06T00:00:00Z",
        "exp": None,
        "note": "",
        "src": "home",
        "nr": 1,
    }


_LIVE = [
    _row(1, "did:claimidx:stranger-h"),
    _row(2, "did:claimidx:stranger-h"),
    _row(3, "did:claimidx:stranger-h"),
    _row(4, "did:claimidx:impl-falsifier-a"),
    _row(5, "did:claimidx:falsifier-b"),
    _row(6, "did:claimidx:agent-5765cb"),
    _row(7, "did:claimidx:seed"),
]


# --- 3: funnel --commons reads the live ledger; the snapshot is a stale fallback ---------


def test_funnel_commons_counts_live_ledger_owners_once(tmp_path: Path, capsys, monkeypatch):
    seen: list[str] = []

    def fake_get(url, timeout=20.0):
        seen.append(url)
        if url.endswith("/api/claims"):
            return json.dumps(_LIVE).encode()
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(home, "_get", fake_get)
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "--fmt", "json", "funnel", "--commons", "--days", "30"]) == 0
    proxy = json.loads(capsys.readouterr().out)["proxy"]
    assert proxy["countable"] == 1, proxy  # the one stranger's 3 rows are one owner; operator/falsifier rows drop
    assert proxy["source"] == "live" and proxy["stale"] is False
    assert proxy["ledger"].endswith("/api/claims")
    assert not [u for u in seen if u == home.DEFAULT_LEDGER]
    assert main(["--db", db, "funnel", "--commons", "--days", "30"]) == 0
    out = capsys.readouterr().out
    assert "commons proxy (live ledger): countable 1/" in out and "STALE" not in out


def test_funnel_commons_falls_back_to_snapshot_with_stale_flag(tmp_path: Path, capsys, monkeypatch):
    snapshot = "\n".join(json.dumps(r) for r in _LIVE[:1] + [_row(9, "did:claimidx:other-stranger")])

    def fake_get(url, timeout=20.0):
        if url.endswith("/api/claims"):
            raise home.HomeError("commons unreachable: connection refused")
        if url == home.DEFAULT_LEDGER:
            return snapshot.encode()
        raise AssertionError(f"unexpected fetch {url}")

    monkeypatch.setattr(home, "_get", fake_get)
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "funnel", "--commons", "--days", "30"]) == 0
    out = capsys.readouterr().out
    assert "WARNING: STALE commons proxy" in out
    assert "commons proxy (repo snapshot, STALE): countable 2/" in out
    assert main(["--db", db, "--fmt", "json", "funnel", "--commons", "--days", "30"]) == 0
    proxy = json.loads(capsys.readouterr().out)["proxy"]
    assert proxy["stale"] is True and proxy["source"] == "snapshot"
    assert "unreachable" in proxy["stale_reason"]
    assert proxy["countable"] == 2


def test_fetch_commons_claims_follows_pages_and_dedupes(monkeypatch):
    pages = {
        "/api/claims": {"claims": _LIVE[:2], "next": "c2"},
        "/api/claims?cursor=c2": {"claims": _LIVE[1:3], "next": None},
    }

    def fake_get(url, timeout=20.0):
        key = url.split("example.test", 1)[1]
        return json.dumps(pages[key]).encode()

    monkeypatch.setattr(home, "_get", fake_get)
    claims, skipped, first = home.fetch_commons_claims("https://example.test")
    assert first == "https://example.test/api/claims"
    assert [c.id for c in claims] == [r["id"] for r in _LIVE[:3]] and not skipped


def test_fetch_commons_claims_bad_json_is_a_home_error(monkeypatch):
    monkeypatch.setattr(home, "_get", lambda url, timeout=20.0: b"<html>502</html>")
    with pytest.raises(home.HomeError):
        home.fetch_commons_claims("https://example.test")


# --- 4: `board` alias only with CLAIMIDX_LEADERBOARD=1 -----------------------------------


def test_board_alias_hidden_without_flag(tmp_path: Path, capsys, monkeypatch):
    from claimidx.cli import build_parser

    monkeypatch.delenv("CLAIMIDX_LEADERBOARD", raising=False)
    text = build_parser().format_help()
    assert not re.search(r"[{,]board[,}]", text) and "alias: board" not in text
    with pytest.raises(SystemExit) as e:
        main(["--db", str(tmp_path / "ix.sqlite"), "board"])
    assert e.value.code == 2
    capsys.readouterr()


def test_board_alias_with_flag(tmp_path: Path, capsys, monkeypatch):
    from claimidx.cli import build_parser

    monkeypatch.setenv("CLAIMIDX_LEADERBOARD", "1")
    monkeypatch.setattr(home, "_get", lambda url, timeout=20.0: json.dumps({"days": 30, "authors": [], "verifiers": []}).encode())
    assert "alias: board" in build_parser().format_help()
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "board"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "apply" not in out


# --- 5: board text never says `apply <id> --yes` ----------------------------------------


def test_board_render_has_no_apply_text():
    from claimidx.board import render_board

    for board in (
        {"days": 30, "authors": [], "verifiers": []},
        {"days": 30, "authors": [{"rank": 1, "own": "did:claimidx:x", "standing": 2, "holds": 2, "verifiers": 1, "claims": 1}], "verifiers": []},
    ):
        text = render_board(board, own="did:claimidx:agent-a")
        assert "apply" not in text and "--yes" not in text.replace("claim --yes --share-yes", "")


# --- 6: inspector links do not 404 ------------------------------------------------------


def _inspector_targets(html: str) -> list[str]:
    urls = re.findall(r"""(?:href|src)=["']([^"']+)["']""", html)
    return [u for u in urls if u.startswith("/") and not u.startswith("//")]


@pytest.mark.parametrize("bare", [False, True])
def test_inspector_links_and_link_header_resolve(tmp_path: Path, monkeypatch, bare):
    from fastapi.testclient import TestClient

    from claimidx import api, discovery

    if bare:  # a pip install: no discovery files beside the package
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.setattr(discovery, "_roots", lambda: [empty])
    client = TestClient(api.create_app(str(tmp_path / "ix.sqlite")))
    r = client.get("/")
    assert r.status_code == 200
    targets = _inspector_targets(r.text)
    link = r.headers.get("link", "")
    targets += re.findall(r"<([^>]+)>", link)
    if bare:
        assert link == ""
    for url in targets:
        got = client.get(url)
        assert got.status_code != 404, url


# --- 7: --scratch never writes a permanent ~/.claimidx identity --------------------------


def test_scratch_writes_no_home_identity(tmp_path: Path, capsys, monkeypatch):
    from claimidx.env import remember_failure

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    monkeypatch.delenv("CLAIMIDX_CONFIG", raising=False)
    monkeypatch.delenv("CLAIMIDX_DB", raising=False)
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("CLAIMIDX_SCRATCH_DIR", str(scratch))
    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    assert main(["--scratch", "--fmt", "json", "claim", "--yes", "--no-diff", "--no-clean-room", "--fix", "pip install json"]) == 0
    cap = capsys.readouterr()
    assert json.loads(cap.out)["id"]
    assert not (fake_home / ".claimidx").exists(), sorted(p.name for p in (fake_home / ".claimidx").iterdir())
    assert (scratch / "index.sqlite").exists()
    import os

    for k in ("CLAIMIDX_CONFIG", "CLAIMIDX_DB", "CLAIMIDX_SCRATCH"):
        assert k not in os.environ, k  # restored on the way out


# --- 8: path_b.next names the claim id -------------------------------------------------


def test_path_b_next_names_the_newest_local_claim(tmp_path: Path):
    from claimidx.impact import path_b_cta

    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-0721b"
    store.log("publish", did, "cix_older", {})
    store.log("publish", did, "cix_newest", {})
    cta = path_b_cta(store, did)
    assert cta["claim_id"] == "cix_newest"
    assert cta["next"] == "claimidx share cix_newest --yes"


# --- 9: MCP leaderboard rejects a non-string `own` with a structured error ----------------


@pytest.mark.parametrize("args,field", [({"own": 123}, "own"), ({"own": ["did:claimidx:x"]}, "own"), ({"days": "soon"}, "days")])
def test_mcp_leaderboard_bad_args_are_structured_errors(tmp_path: Path, monkeypatch, args, field):
    from claimidx.mcp_server import handle

    seen: list[str] = []
    monkeypatch.setattr(home, "_get", lambda url, timeout=20.0: seen.append(url) or b"{}")
    for flag in ("0", "1"):
        monkeypatch.setenv("CLAIMIDX_LEADERBOARD", flag)
        msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claimidx_leaderboard", "arguments": args}}
        rec = handle(msg, Store(str(tmp_path / "ix.sqlite")))
        res = rec["result"]
        assert res.get("isError") is True
        sc = res["structuredContent"]
        assert sc["ok"] is False and sc["error"] == "invalid-argument" and sc["field"] == field
    assert not seen


# --- 10: never replay.held true with recorded false --------------------------------------


@pytest.fixture
def no_bwrap(monkeypatch):
    real = shutil.which
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, *a, **k: None if name == "bwrap" else real(name, *a, **k))


def _walk_replays(obj):
    if isinstance(obj, dict):
        if "held" in obj and "recorded" in obj:
            yield obj
        for v in obj.values():
            yield from _walk_replays(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_replays(v)


def test_claim_gate_refusal_replay_is_not_held(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    argv = ["--db", db, "--fmt", "json", "claim", "--err", "ModuleNotFoundError: No module named 'json'", "--cwd", str(tree)]
    argv += ["--eval", 'python -I -c "import json"', "--no-diff", "--fix", "pip install json", "--yes"]
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    rep = out["replay"]
    assert rep["recorded"] is False and rep["held"] is False
    assert rep.get("eval_passed") is True
    for r in _walk_replays(out["replay"]):
        assert not (r["held"] is True and r["recorded"] is False), r
