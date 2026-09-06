"""`claimidx run -- <cmd>`: the sensor for harnesses without hooks."""

from __future__ import annotations

import sys
from pathlib import Path

from claimidx.cli import main
from claimidx.env import last_failure


def test_run_passes_status_through_and_advises_on_stderr(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    app = tmp_path / "app.py"
    app.write_text("import no_such_mod_cix_run\n", encoding="utf-8")
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ModuleNotFoundError" in captured.out  # the command's own output, untouched
    assert captured.err.splitlines()[-1].startswith("CLAIMIDX verdict solve")
    rec = last_failure()
    assert rec and rec["command"].endswith("app.py") and rec["err"].startswith("ModuleNotFoundError")
    # The fix: same command passes -> one nudge, then quiet.
    app.write_text("print('fixed')\n", encoding="utf-8")
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    captured = capsys.readouterr()
    assert rc == 0 and "fixed" in captured.out
    assert captured.err.startswith("CLAIMIDX fixed:") and "claimidx claim --yes" in captured.err
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    assert rc == 0 and capsys.readouterr().err == ""


def test_run_with_a_hit_names_the_claim(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'no_such_mod_cix_run2'"
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
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
                rt,
                "--fix-k",
                "constraint",
                "--fix-b",
                "no-such-mod-cix-run2",
                "--eval",
                'python -c "import no_such_mod_cix_run2"',
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import no_such_mod_cix_run2\n", encoding="utf-8")
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "app.py"])
    err_out = capsys.readouterr().err
    assert rc == 1 and f"CLAIMIDX verdict apply {cid}" in err_out and "not instructions" in err_out


def test_run_never_uses_a_shell(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    marker = tmp_path / "pwned"
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", "echo", "x;", "touch", str(marker)])
    capsys.readouterr()
    assert rc == 0 and not marker.exists()
