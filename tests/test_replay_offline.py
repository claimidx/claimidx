"""Offline replay and vacuous holds. A block is not a fail. A networked replay does not mint nr."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from claimidx.api import create_app
from claimidx.cli import main
from claimidx.fingerprint import fingerprint
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.sandbox import ReplayResult


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def _publish(db: str, capsys, *, err: str, eval_cmd: str) -> str:
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
                "ok",
                "--eval",
                eval_cmd,
            ]
        )
        == 0
    )
    return capsys.readouterr().out.strip()


def _confirm(db: str, capsys, cid: str, *, cwd: str | None = None, extra: list[str] | None = None) -> tuple[int, dict]:
    args = ["--db", db, "--fmt", "json", "confirm", "--replay", *(extra or [])]
    if cwd:
        args += ["--cwd", cwd]
    rc = main(args + [cid])
    return rc, json.loads(capsys.readouterr().out)


def _show(db: str, capsys, cid: str) -> dict:
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    return json.loads(capsys.readouterr().out)


def _claim(err: str, eval_cmd: str, *, rt: str | None = None) -> Claim:
    rt = rt or _py_rt()
    return Claim(
        fp=fingerprint(err=err, eco="py", rt=rt),
        cls="type_error" if err.startswith("TypeError") else "module_not_found",
        err=err,
        eco="py",
        rt=rt,
        fix=Fix(k="constraint", b="ok"),
        eval=EvalSpec(cmd=eval_cmd),
    )


def test_vacuous_reason_patterns():
    from claimidx.gate import vacuous_reason

    assert vacuous_reason("10 passed") == ""
    assert vacuous_reason("100 passed") == ""
    assert vacuous_reason("0 passed").startswith("vacuous-pass")
    assert vacuous_reason("0 skipped") == "vacuous-pass: skipped target"
    assert vacuous_reason("3 passed, 0 skipped") == ""
    assert vacuous_reason("0 skipped\n3 passed") == ""
    assert vacuous_reason("") == ""
    assert vacuous_reason("collected 0 items") == "vacuous-pass: collected 0 items"
    assert vacuous_reason("no tests ran") == "vacuous-pass: no tests ran"
    assert vacuous_reason("Executed 0 of 0") == "vacuous-pass: Executed 0 of 0"


def test_network_blocked_is_false_when_net_is_open():
    from claimidx.sandbox import network_blocked

    open_miss = ReplayResult(True, True, 1, 0, False, "miss", stderr="URLError: timed out", net="open")
    assert network_blocked(open_miss) is False
    held = ReplayResult(True, True, 0, 0, True, "held", stderr="URLError", net="offline")
    assert network_blocked(held) is False
    blocked = ReplayResult(True, True, 1, 0, False, "miss", stderr="claimidx-replay-offline", net="offline")
    assert network_blocked(blocked) is True


def test_reruns_refuse_before_target():
    from claimidx.gate import graduation_gate

    err = "ModuleNotFoundError: No module named 'bind_target'"
    claim = _claim(err, "python -m pytest --reruns 2")
    claim.cls = "module_not_found"
    held = ReplayResult(True, True, 0, 0, True, "held", env=_py_rt(), net="offline", net_how="interpreter")
    decision = graduation_gate(claim, held)
    assert decision.mint_nr is False
    assert decision.reason.startswith("reruns")


def test_unenforced_https_recipe_does_not_mint():
    from claimidx.gate import graduation_gate

    err = "TypeError: flags left the network open"
    claim = _claim(err, "python -S -c \"print('https://example.com')\"")
    held = ReplayResult(
        True,
        True,
        0,
        0,
        True,
        "held",
        stdout="https://example.com\n",
        env=_py_rt(),
        net="unenforced",
        net_how="python-flags",
    )
    decision = graduation_gate(claim, held)
    assert decision.mint_nr is False
    assert decision.reason.startswith("replay-net-unenforced")


def test_empty_stdout_pin_shape_is_not_vacuous(tmp_path: Path):
    from claimidx.gate import graduation_gate

    claim = _claim("TypeError: pin probe", 'python -c "print(1)"')
    held = ReplayResult(True, True, 0, 0, True, "held-pin", env=_py_rt())
    decision = graduation_gate(claim, held, cwd=str(tmp_path))
    assert decision.mint_nr is True
    assert "vacuous" not in decision.reason
    assert "net-open" not in decision.reason


def test_confirm_replay_vacuous_pass_does_not_mint_or_fail(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(
        db,
        capsys,
        err="TypeError: the suite printed a pass and ran nothing",
        eval_cmd="python -c \"print('collected 0 items'); print('0 passed')\"",
    )
    rc, out = _confirm(db, capsys, cid)
    assert rc == 2
    assert out["held"] is False and out["eval_passed"] is True
    assert out["recorded"] is False
    assert out["replay"]["held"] is False and out["replay"]["recorded"] is False
    assert str(out.get("reason") or "").startswith("vacuous-pass")
    shown = _show(db, capsys, cid)
    assert shown["nr"] == 0
    assert shown["nf"] == 0


def test_confirm_replay_local_pytest_mints(tmp_path: Path, capsys):
    tree = tmp_path / "proj"
    tree.mkdir()
    (tree / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tree / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(
        db,
        capsys,
        err="TypeError: arithmetic drifted",
        eval_cmd="python -m pytest -q test_ok.py",
    )
    rc, out = _confirm(db, capsys, cid, cwd=str(tree))
    assert rc == 0, out
    assert out["held"] is True
    assert out.get("nr") == 1 or out.get("claim", {}).get("nr") == 1
    shown = _show(db, capsys, cid)
    assert shown["nr"] == 1
    assert shown["nf"] == 0


def test_confirm_replay_urlopen_is_offline_block(tmp_path: Path, capsys):
    # urllib and socket are not allowlisted evals, so the probe uses http.client.
    # That still calls create_connection, which the offline interpreter hook blocks.
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(
        db,
        capsys,
        err="TypeError: the probe dialed out",
        eval_cmd="python -c \"import http.client; http.client.HTTPSConnection('example.com').request('GET', '/')\"",
    )
    rc, out = _confirm(db, capsys, cid)
    assert rc == 2
    assert out["held"] is False
    assert out["recorded"] is False
    assert str(out.get("reason") or "").startswith("replay-offline")
    shown = _show(db, capsys, cid)
    assert shown["nr"] == 0
    assert shown["nf"] == 0


def test_confirm_replay_net_open_does_not_mint(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(
        db,
        capsys,
        err="TypeError: net flag stays recorded off the ledger count",
        eval_cmd='python -c "print(1)"',
    )
    rc, out = _confirm(db, capsys, cid, extra=["--net"])
    # 0.7.16: --net is recorded on the confirm detail (net=open), as PROTOCOL promises; it never mints nr.
    assert rc == 0
    assert out["held"] is True
    assert out["recorded"] is True
    assert out["nr_minted"] is False
    assert str(out.get("reason") or "").startswith("net-open")
    assert out["replay"]["net"] == "open"
    shown = _show(db, capsys, cid)
    assert shown["nr"] == 0
    assert shown["nf"] == 0
    assert shown["nc"] == 1


def test_api_confirm_net_open_records_without_nr(tmp_path: Path):
    app = create_app(str(tmp_path / "home.sqlite"))
    client = TestClient(app)
    posted = client.post(
        "/api/publish",
        json={
            "err": "TypeError: home net flag",
            "fix_k": "constraint",
            "fix_b": "ok",
            "eval": 'python -c "print(1)"',
            "own": "did:claimidx:agent-c",
            "rt": "py@3.13",
        },
    )
    assert posted.status_code == 200, posted.text
    cid = posted.json()["claim"]["id"]
    ok = client.post(f"/api/claims/{cid}/confirm?replay=true&net=open&own=did:claimidx:agent-c")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["held"] is True
    assert body["recorded"] is True
    assert body["nr_minted"] is False
    assert str(body.get("reason") or "").startswith("net-open")
    assert "replay" not in body
    assert body["claim"]["nr"] == 0
    assert body["claim"]["nc"] == 1
