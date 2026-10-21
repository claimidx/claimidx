"""0.7.18: follow-ups from the 0.7.17 implementation run.

1. The ask/run/board nudges name the sharing command (`claim --yes --share-yes`), not `claim --yes` alone.
2. The leaderboard tool description says the default makes no network call; only CLAIMIDX_LEADERBOARD=1 does.
3. With the commons disabled, the share review says so instead of destination=commons.
4. Without bwrap, a confirm --replay *miss* records nothing (no nf), like an unenforced hold.
5. claim --share-yes reports one share status (the nested replay.share matches the top level).
7. `serve` GET / falls back to the inspector copy packaged in the wheel.
+  claim results carry top-level nc/nf.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from claimidx import sandbox
from claimidx.cli import main
from claimidx.store import Store

_UNENFORCED_MISS = 'python -I -c "raise SystemExit(3)"'


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


@pytest.fixture
def no_bwrap(monkeypatch):
    real = shutil.which
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, *a, **k: None if name == "bwrap" else real(name, *a, **k))


def _publish(db: str, capsys, *, err: str, eval_cmd: str = _UNENFORCED_MISS) -> str:
    argv = ["--db", db, "--fmt", "id", "publish", "--err", err, "--eco", "py", "--rt", _py_rt()]
    argv += ["--fix-k", "constraint", "--fix-b", "ok", "--eval", eval_cmd]
    assert main(argv) == 0
    return capsys.readouterr().out.strip()


def _kinds(db: str) -> list[str]:
    return [e.get("kind") or e.get("ev") or "" for e in Store(db).events(limit=50)]


# --- 1 ---------------------------------------------------------------------------------


def test_nudges_point_at_the_sharing_command():
    from claimidx import board, hook, match, runwrap

    for mod in (hook, runwrap, match, board):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "claim --yes --share-yes" in src, mod.__name__
        assert "claim --yes so the next agent skips this" not in src, mod.__name__


# --- 2 ---------------------------------------------------------------------------------


def test_leaderboard_description_is_accurate_about_network():
    from claimidx.mcp_server import TOOLS

    desc = next(t for t in TOOLS if t["name"] == "claimidx_leaderboard")["description"]
    assert "network: GET" not in desc
    assert "By default it makes no network call" in desc and "CLAIMIDX_LEADERBOARD=1" in desc


# --- 3 ---------------------------------------------------------------------------------


def _claim_obj():
    from claimidx.fingerprint import fingerprint
    from claimidx.models import Claim, EvalSpec, Fix

    err = "TypeError: commons review probe"
    return Claim(
        fp=fingerprint(err=err, eco="py", rt=_py_rt()),
        cls="type_error",
        err=err,
        eco="py",
        rt=_py_rt(),
        fix=Fix(k="constraint", b="ok"),
        eval=EvalSpec(cmd="true"),
    )


def test_review_says_commons_disabled(monkeypatch):
    from claimidx.home import commons_review, format_commons_review

    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    c = _claim_obj()
    r = commons_review(c)
    assert r["destination"] == "none" and r["commons"] == "disabled"
    text = format_commons_review(c)
    assert "destination=commons" not in text and "commons disabled" in text and "CLAIMIDX_COMMONS=0" in text


def test_review_says_commons_when_on(monkeypatch):
    from claimidx.home import commons_review, format_commons_review

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    c = _claim_obj()
    assert commons_review(c)["destination"] == "commons"
    assert "destination=commons" in format_commons_review(c)


def test_cli_claim_share_yes_with_commons_off_says_disabled(tmp_path: Path, capsys, monkeypatch):
    from claimidx import home
    from claimidx.env import remember_failure

    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: calls.append(url) or {})
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--share-yes", "--no-diff", "--fix", "pip install json"]) == 0
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["ok"] and out["commons"]["status"] == "disabled"
    assert "destination=commons" not in cap.err and "commons disabled" in cap.err
    assert not calls


def test_mcp_claim_share_yes_with_commons_off_says_disabled(tmp_path: Path, monkeypatch):
    from claimidx import home
    from claimidx.env import remember_failure
    from claimidx.mcp_server import _call

    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    monkeypatch.setattr(home, "_post", lambda *a, **k: pytest.fail("no network when the commons is off"))
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    store = Store(str(tmp_path / "ix.sqlite"))
    out = _call("claimidx_claim", {"yes": True, "share_yes": True, "no_diff": True, "fix": "pip install json", "cwd": str(tree)}, store)
    assert out["ok"] and out["commons"]["status"] == "disabled"
    assert (out.get("review") or {}).get("destination", "none") == "none"


# --- 4 ---------------------------------------------------------------------------------


def test_cli_confirm_unenforced_miss_records_no_fail(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: an unenforced miss is not evidence")
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", cid])
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert rc == 2
    assert out["held"] is False and out["recorded"] is False
    assert out["error"] == "replay-net-unenforced" and out["reason"].startswith("replay-net-unenforced")
    assert out["replay"]["net"] == "unenforced"
    assert "OFFLINE REPLAY IS NOT ENFORCED" in cap.err
    c = Store(db).get(cid)
    assert c.nf == 0 and c.nc == 0 and c.nr == 0
    assert "fail" not in _kinds(db)


def test_cli_confirm_unenforced_miss_text_mode(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: an unenforced miss in text mode")
    assert main(["--db", db, "confirm", "--replay", cid]) == 2
    err = capsys.readouterr().err
    assert "not recorded: replay-net-unenforced" in err
    assert Store(db).get(cid).nf == 0


def test_cli_confirm_net_miss_is_still_recorded(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: an explicit --net miss counts")
    main(["--db", db, "--fmt", "json", "confirm", "--replay", "--net", cid])
    capsys.readouterr()
    assert Store(db).get(cid).nf == 1


def test_mcp_confirm_unenforced_miss_is_error_and_records_no_fail(tmp_path: Path, capsys, no_bwrap):
    from claimidx.mcp_server import handle

    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: mcp unenforced miss")
    store = Store(db)
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claimidx_confirm", "arguments": {"id": cid, "replay": True}}}
    result = handle(msg, store)["result"]
    assert result.get("isError") is True
    body = result["structuredContent"]
    assert body["recorded"] is False and body["error"] == "replay-net-unenforced"
    assert store.get(cid).nf == 0


def test_batch_verify_unenforced_miss_records_no_fail(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, err="TypeError: batch unenforced miss")
    main(["--db", db, "--fmt", "json", "verify", "--apply", "--id", cid])
    capsys.readouterr()
    assert Store(db).get(cid).nf == 0
    assert "fail" not in _kinds(db)


def test_unenforced_miss_refusal_only_for_unenforced_misses():
    from claimidx.gate import unenforced_miss_refusal

    R = sandbox.ReplayResult
    assert unenforced_miss_refusal(R(True, True, 1, 0, False, "miss", net="unenforced"))["error"] == "replay-net-unenforced"
    assert unenforced_miss_refusal(R(True, True, 1, 0, False, "miss", net="offline")) is None
    assert unenforced_miss_refusal(R(True, True, 1, 0, False, "miss", net="open")) is None
    assert unenforced_miss_refusal(R(True, True, 0, 0, True, "held", net="unenforced")) is None


# --- 5 + nc/nf --------------------------------------------------------------------------


def test_claim_share_yes_reports_one_share_status_and_counts(tmp_path: Path, capsys, monkeypatch):
    from claimidx import home
    from claimidx.env import remember_failure

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: {"exists": False, "claim": {"id": payload.get("id")}})
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--share-yes", "--no-diff", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["share"]["status"] == "commons"
    nested = (out.get("replay") or {}).get("share")
    assert nested is not None and nested["status"] == out["share"]["status"]
    c = Store(db).get(out["id"])
    assert out["nc"] == c.nc and out["nf"] == c.nf


# --- 7 ---------------------------------------------------------------------------------


def test_serve_root_falls_back_to_packaged_inspector(tmp_path: Path, monkeypatch):
    from fastapi.testclient import TestClient

    from claimidx import api

    monkeypatch.setattr(api, "WEB", tmp_path / "missing" / "index.html")
    assert api.WEB_PACKAGED.exists()
    r = TestClient(api.create_app(str(tmp_path / "ix.sqlite"))).get("/")
    assert r.status_code == 200 and "inspector missing" not in r.text and "/api/stats" in r.text


def test_unenforced_miss_refusal_ignores_runs_that_did_not_run():
    from claimidx.gate import unenforced_miss_refusal

    assert unenforced_miss_refusal(sandbox.ReplayResult(True, False, None, 0, False, "eval-precondition", net="unenforced")) is None
