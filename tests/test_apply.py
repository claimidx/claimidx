"""`claimidx apply`: verdict to recorded hold in one command, never an executor."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from claimidx.cli import main
from claimidx.store import Store


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def test_apply_plan_then_yes_for_a_patch(tmp_path: Path, capsys):
    tree = tmp_path / "repo"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / "mod.py").write_text("import json\n\ndef load(t):\n    return json.loads(t, encoding='utf-8')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "broken"], cwd=tree, check=True)
    (tree / "mod.py").write_text("import json\n\ndef load(t):\n    return json.loads(t)\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "json",
                "claim",
                "--err",
                "TypeError: loads() got an unexpected keyword argument 'encoding'",
                "--cwd",
                str(tree),
                "--eval",
                "python -c \"import mod; mod.load('{}')\"",
                "--yes",
                "--no-replay",
            ]
        )
        == 0
    )
    cid = json.loads(capsys.readouterr().out)["id"]
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tree), str(other)], check=True)
    # Plan only.
    assert main(["--db", db, "--fmt", "json", "apply", cid, "--cwd", str(other)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and out["plan"]["steps"][0][:2] == ["git", "apply"] and out["plan"]["stdin"].startswith("diff --git")
    assert "encoding=" in (other / "mod.py").read_text(encoding="utf-8")
    # Execute.
    assert main(["--db", db, "--fmt", "json", "apply", cid, "--cwd", str(other), "--yes"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is True and out["replay"]["recorded"] is True and out["replay"]["nr"] == 1
    assert "encoding=" not in (other / "mod.py").read_text(encoding="utf-8")


def test_apply_never_executes_cmd_or_prose(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "RuntimeError: apply cmd probe",
                "--eco",
                "py",
                "--fix-k",
                "cmd",
                "--fix-b",
                "git clean -fdx",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "apply", cid, "--cwd", str(tmp_path), "--yes"]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["applied"] is False and "never executed" in out["manual"]
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "RuntimeError: apply prose probe",
                "--eco",
                "py",
                "--fix-k",
                "patch",
                "--fix-b",
                "move the import above the app factory",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "apply", cid, "--cwd", str(tmp_path), "--yes"]) == 3
    assert "by hand" in json.loads(capsys.readouterr().out)["manual"]


def test_apply_refuses_pin_specs_that_are_not_plain(tmp_path: Path):
    from claimidx.apply import plan
    from claimidx.fingerprint import fingerprint
    from claimidx.models import Claim, EvalSpec, Fix

    def claim(spec: str) -> Claim:
        err = "ModuleNotFoundError: No module named 'x'"
        return Claim(fp=fingerprint(err=err, eco="py"), cls="module_not_found", err=err, eco="py", fix=Fix(k="pin", b=spec), eval=EvalSpec(cmd="true"))

    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    assert plan(claim("x==1.2.3"), str(tmp_path))["steps"][0][-1] == "x==1.2.3"
    assert plan(claim("x[extra]>=1,<2"), str(tmp_path))["steps"]
    for bad in ("git+https://evil/x.git", "-e .", "x --index-url https://evil", "x; rm -rf /"):
        assert plan(claim(bad), str(tmp_path)).get("manual"), bad


def test_apply_flags_claims_not_published_here(tmp_path: Path, capsys):
    from claimidx.fingerprint import fingerprint
    from claimidx.models import Claim, EvalSpec, Fix

    db = str(tmp_path / "ix.sqlite")
    err = "RuntimeError: apply trust probe"
    c = Claim(
        fp=fingerprint(err=err, eco="py", rt=_py_rt()),
        cls="other",
        err=err,
        eco="py",
        rt=_py_rt(),
        fix=Fix(k="patch", b="prose"),
        eval=EvalSpec(cmd="true"),
        own="did:claimidx:them",
        src="home",
    )
    Store(db).put(c)
    assert main(["--db", db, "--fmt", "json", "apply", c.id, "--cwd", str(tmp_path)]) == 3
    out = json.loads(capsys.readouterr().out)
    assert out["trusted"] is False


def test_mcp_apply_plan(tmp_path: Path, capsys):
    from claimidx.mcp_server import _call

    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "RuntimeError: mcp apply probe",
                "--eco",
                "py",
                "--fix-k",
                "constraint",
                "--fix-b",
                "tomli",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    out = _call("claimidx_apply", {"id": cid, "cwd": str(tmp_path)}, Store(db))
    assert out["applied"] is False and out["trusted"] is True and ("manual" in out or "hint" in out)
