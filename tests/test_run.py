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
    # With a shell, `;` would run the second command and create the marker. Without one, python gets three extra argv
    # entries and prints x. The interpreter is the executable so this holds under Git Bash and PowerShell alike (no `echo` binary).
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", sys.executable, "-c", "print('x')", ";", "touch", str(marker)])
    captured = capsys.readouterr()
    assert rc == 0 and "x" in captured.out and not marker.exists()


def test_run_resolves_the_head_like_a_shell_and_a_spawn_failure_is_not_a_tree_failure(tmp_path: Path, monkeypatch, capsys):
    """`claimidx run -- gradle …` must find gradle.cmd on Windows; a command that cannot start is the wrapper's failure, not the tree's."""
    import os

    from claimidx.env import forget_failure

    db = str(tmp_path / "ix.sqlite")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    if os.name == "nt":
        (bindir / "cixhello.cmd").write_text("@echo hello-from-cmd\r\n", encoding="utf-8")
    else:
        script = bindir / "cixhello"
        script.write_text("#!/bin/sh\necho hello-from-cmd\n", encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    forget_failure()
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", "cixhello"])
    captured = capsys.readouterr()
    assert rc == 0 and "hello-from-cmd" in captured.out
    rc = main(["--db", db, "run", "--cwd", str(tmp_path), "--", "no-such-command-cix-zz"])
    captured = capsys.readouterr()
    assert rc == 127 and "claimidx run:" in captured.err
    assert "CLAIMIDX verdict" not in captured.err
    assert last_failure() is None
