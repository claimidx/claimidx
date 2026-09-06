"""`verdict` is the first thing an ask returns: one action, ten words of why, one next command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claimidx.cli import main


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def test_miss_verdict_says_solve(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "--fmt", "json", "ask", "--err", "RuntimeError: nothing like this exists"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert list(out.keys())[0] == "verdict"
    assert out["verdict"]["action"] == "solve"
    assert "claimidx claim" in out["verdict"]["next"]


def test_hit_verdict_replay_then_apply(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'json'"
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
    # Retrieved only: replay first.
    assert main(["--db", db, "--fmt", "json", "ask", "--err", err, "--eco", "py", "--rt", _py_rt()]) == 0
    out = json.loads(capsys.readouterr().out)
    v = out["verdict"]
    assert v["action"] == "apply" and v["id"] == cid
    assert "exact match" in v["why"] and "retrieved" in v["why"]
    assert v["next"].startswith(f"claimidx apply {cid}")
    # Reproduced: apply.
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "ask", "--err", err, "--eco", "py", "--rt", _py_rt()]) == 0
    v = json.loads(capsys.readouterr().out)["verdict"]
    assert v["action"] == "apply"
    assert f"reproduced on {_py_rt()}" in v["why"]
    # Dense output leads with the verdict line.
    assert main(["--db", db, "ask", "--err", err, "--eco", "py", "--rt", _py_rt()]) == 0
    first = capsys.readouterr().out.splitlines()[0]
    assert first.startswith(f"# verdict apply {cid}")


def test_hook_context_leads_with_verdict(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    payload = json.dumps({"hook_event_name": "PostToolUseFailure", "tool_response": {"stderr": "RuntimeError: hook verdict probe"}})
    assert main(["--db", db, "hook", "--err", payload]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.splitlines()[0].startswith("CLAIMIDX verdict solve")


def test_mcp_ask_and_hook_carry_verdict(tmp_path: Path):
    from claimidx.mcp_server import _call
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    out = _call("claimidx_ask", {"err": "RuntimeError: mcp verdict probe"}, store)
    assert list(out.keys())[0] == "verdict" and out["verdict"]["action"] == "solve"
    out = _call("claimidx_hook", {"err": "RuntimeError: mcp verdict probe"}, store)
    assert out["verdict"]["action"] == "solve"


def test_replay_of_an_unapplied_pin_fix_is_not_a_fail(tmp_path: Path, capsys):
    """`import x` missing when the remedy is `x==1.2` means nobody installed it yet: nothing recorded, apply step suggested."""
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'no_such_pkg_cix'"
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
                "pin",
                "--fix-b",
                "no-such-pkg-cix==1.0.0",
                "--eval",
                'python -c "import no_such_pkg_cix"',
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", cid])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["recorded"] is False
    assert out["reason"].startswith("fix-not-applied")
    assert out["suggest"]["fix"] == "no-such-pkg-cix==1.0.0"
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    assert json.loads(capsys.readouterr().out)["nf"] == 0


def test_pulled_and_never_held_is_review_not_apply(tmp_path: Path, capsys):
    from claimidx.fingerprint import fingerprint
    from claimidx.models import Claim, EvalSpec, Fix
    from claimidx.store import Store

    db = str(tmp_path / "ix.sqlite")
    err = "RuntimeError: pulled review probe"
    Store(db).put(
        Claim(
            fp=fingerprint(err=err, eco="py"),
            cls="other",
            err=err,
            eco="py",
            fix=Fix(k="patch", b="x"),
            eval=EvalSpec(cmd="true"),
            own="did:claimidx:them",
            src="home",
        )
    )
    assert main(["--db", db, "--fmt", "json", "ask", "--err", err, "--eco", "py"]) == 0
    v = json.loads(capsys.readouterr().out)["verdict"]
    assert v["action"] == "review" and "unverified" in v["why"]


def test_family_match_when_the_asker_did_not_know_the_dep(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    err = "TypeError: load() missing 1 required positional argument: 'Loader'"
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
                "--dep",
                "PyYAML@6.0.3",
                "--fix-k",
                "patch",
                "--fix-b",
                "use yaml.safe_load",
                "--eval",
                "python -m pytest -q",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "ask", "--err", err, "--eco", "py", "--rt", _py_rt()]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["claims"][0]["match"] == "family"
    assert "family match" in out["verdict"]["why"]
    assert main(["--db", db, "--fmt", "json", "ask", "--err", err, "--eco", "py", "--rt", _py_rt(), "--dep", "PyYAML@5.4.1"]) == 0
    assert json.loads(capsys.readouterr().out)["claims"][0]["match"] == "similar"
