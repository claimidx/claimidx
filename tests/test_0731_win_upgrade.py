"""0.7.31: leftover ~laimidx dirs and a Windows-safe claimidx upgrade."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claimidx.cli import main
from claimidx.upgrade import (
    MCP_RUNNING_EXIT,
    format_pip_upgrade_command,
    leftover_dirs,
    leftover_report,
    run_upgrade,
)


def test_leftover_dirs_detects_tilde_renames(tmp_path: Path):
    site = tmp_path / "site-packages"
    (site / "~laimidx").mkdir(parents=True)
    (site / "~-aimidx-0.7.29.dist-info").mkdir()
    (site / "~~aimidx.dist-info").mkdir()
    (site / "claimidx").mkdir()
    (site / "claimidx-0.7.30.dist-info").mkdir()
    (site / "~otherpkg").mkdir()
    found = {p.name for p in leftover_dirs([site])}
    assert "~laimidx" in found
    assert "~-aimidx-0.7.29.dist-info" in found
    assert "~~aimidx.dist-info" in found
    assert "claimidx" not in found
    assert "claimidx-0.7.30.dist-info" not in found
    assert "~otherpkg" not in found
    rec = leftover_report([site])
    assert rec["ok"] is False
    assert rec["fix"]
    assert "force-reinstall" in rec["fix"]
    assert leftover_report([tmp_path / "empty"])["ok"] is True


def test_doctor_reports_pip_leftovers_additively(tmp_path: Path, capsys, monkeypatch):
    site = tmp_path / "site-packages"
    (site / "~laimidx").mkdir(parents=True)
    (site / "~-aimidx").mkdir()
    monkeypatch.setattr("claimidx.upgrade.site_package_dirs", lambda: [site])
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "--fmt", "json", "doctor"])
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["package_version"]
    assert "server_info_version" in body
    assert "version_mismatch" in body
    leftovers = body["pip_leftovers"]
    assert leftovers["ok"] is False
    names = [Path(p).name for p in leftovers["paths"]]
    assert "~laimidx" in names and "~-aimidx" in names
    check = next(c for c in body["checks"] if c["name"] == "pip-leftovers")
    assert check["ok"] is False
    assert "force-reinstall" in check["detail"]
    assert "Ignoring invalid distribution" in check["detail"]
    assert rc == 2


def test_doctor_leftovers_check_ok_when_site_is_clean(tmp_path: Path, capsys, monkeypatch):
    site = tmp_path / "site-packages"
    (site / "claimidx").mkdir(parents=True)
    monkeypatch.setattr("claimidx.upgrade.site_package_dirs", lambda: [site])
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "--fmt", "json", "doctor"])
    body = json.loads(capsys.readouterr().out)
    assert body["pip_leftovers"]["ok"] is True
    assert body["pip_leftovers"]["paths"] == []
    check = next(c for c in body["checks"] if c["name"] == "pip-leftovers")
    assert check["ok"] is True
    assert rc in (0, 2)
    assert "package_version" in body and "version_mismatch" in body


def test_upgrade_windows_mcp_running_warns_and_exits_nonzero(capsys, monkeypatch):
    monkeypatch.setattr("claimidx.upgrade.list_mcp_processes", lambda: [{"name": "claimidx-mcp.exe"}])
    monkeypatch.setattr("claimidx.upgrade.os.name", "nt")
    called: list = []
    monkeypatch.setattr("claimidx.upgrade.subprocess.run", lambda *a, **k: called.append(a) or None)
    rc = main(["upgrade"])
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert rc == MCP_RUNNING_EXIT == 2
    assert called == []
    assert "claimidx-mcp is running" in text
    assert "WinError 32" in text


def test_upgrade_windows_mcp_running_message(monkeypatch):
    result = run_upgrade(windows=True, processes=[{"name": "claimidx-mcp.exe"}], yes=True)
    assert result["rc"] == 2
    assert result["ran_pip"] is False
    assert "claimidx-mcp is running" in result["stderr"]
    assert "WinError 32" in result["stderr"]
    assert "MCP client" in result["stderr"]


def test_upgrade_windows_idle_prints_command_and_does_not_spawn(capsys, monkeypatch):
    monkeypatch.setattr("claimidx.upgrade.list_mcp_processes", lambda: [])
    monkeypatch.setattr("claimidx.upgrade.os.name", "nt")

    def boom(*_a, **_k):
        raise AssertionError("pip must not be spawned on Windows")

    monkeypatch.setattr("claimidx.upgrade.subprocess.run", boom)
    rc = main(["upgrade", "--yes"])
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert rc == 0
    assert format_pip_upgrade_command() in text
    assert "-m pip install -U claimidx" in text
    assert "normal shell" in text


def test_upgrade_posix_without_yes_prints_only(capsys, monkeypatch):
    monkeypatch.setattr("claimidx.upgrade.list_mcp_processes", lambda: [])
    monkeypatch.setattr("claimidx.upgrade.os.name", "posix")

    def boom(*_a, **_k):
        raise AssertionError("pip must not be spawned without --yes")

    monkeypatch.setattr("claimidx.upgrade.subprocess.run", boom)
    rc = main(["upgrade"])
    text = capsys.readouterr().out
    assert rc == 0
    assert "-m pip install -U claimidx" in text
    assert "--yes" in text


def test_upgrade_posix_yes_runs_pip(monkeypatch):
    monkeypatch.setattr("claimidx.upgrade.list_mcp_processes", lambda: [])
    monkeypatch.setattr("claimidx.upgrade.os.name", "posix")

    class _Proc:
        returncode = 0

    seen: list[list[str]] = []

    def fake(argv, check=False):  # noqa: ARG001
        seen.append(list(argv))
        return _Proc()

    monkeypatch.setattr("claimidx.upgrade.subprocess.run", fake)
    result = run_upgrade(yes=True, windows=False, processes=[])
    assert result["ran_pip"] is True
    assert result["rc"] == 0
    assert seen == [[sys.executable, "-m", "pip", "install", "-U", "claimidx"]]

    seen.clear()
    rc = main(["upgrade", "--yes"])
    assert rc == 0
    assert seen == [[sys.executable, "-m", "pip", "install", "-U", "claimidx"]]


def test_upgrade_dry_run_prints_only_on_any_os(monkeypatch):
    monkeypatch.setattr("claimidx.upgrade.subprocess.run", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no pip")))
    for windows, running in ((True, True), (True, False), (False, False)):
        result = run_upgrade(
            yes=True,
            dry_run=True,
            windows=windows,
            processes=[{"name": "claimidx-mcp.exe"}] if running else [],
        )
        assert result["rc"] == 0
        assert result["ran_pip"] is False
        assert "-m pip install -U claimidx" in result["stdout"]
        if running:
            assert "claimidx-mcp" in result["stdout"]
