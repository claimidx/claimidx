"""Upgrade helpers: detect pip leftover dirs, and a cross-platform `claimidx upgrade`.

On Windows, `pip install -U` can hit WinError 32 while `claimidx-mcp.exe` is held
open by an MCP client, and pip then leaves `~laimidx*`-style dirs behind. This
module never runs pip in-process on Windows (that would lock `claimidx.exe`).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

# Documented: upgrade refuses to print-or-run the pip command on Windows while
# claimidx-mcp is still running. 2 is the usual ClaimIDX "cannot complete this
# command; the user needs to act" status (same family as doctor-not-clean).
MCP_RUNNING_EXIT = 2

PIP_UPGRADE = ("-m", "pip", "install", "-U", "claimidx")
RECOVERY_CMD = "py -m pip install --force-reinstall claimidx"

# pip's Windows rename of a locked `claimidx` dist: ~laimidx, ~-aimidx, ~~aimidx, …
_LEFTOVER = re.compile(r"^~.*aimidx", re.IGNORECASE)

_CLIENTS = "your MCP client (Cursor, Claude Code, or another editor or desktop app)"


def pip_upgrade_command(executable: str | None = None) -> list[str]:
    return [executable or sys.executable, *PIP_UPGRADE]


def format_pip_upgrade_command(executable: str | None = None) -> str:
    """Shell-ready form: \"<sys.executable>\" -m pip install -U claimidx."""
    return f'"{executable or sys.executable}" -m pip install -U claimidx'


def leftover_fix(paths: list[str] | None = None) -> str:
    listed = "then "
    if paths:
        listed = "delete " + ", ".join(paths) + ", then "
    return f"Quit {_CLIENTS} or stop claimidx-mcp, {listed}run `{RECOVERY_CMD}`. pip may also print 'Ignoring invalid distribution ~laimidx'."


def site_package_dirs() -> list[Path]:
    """site.getsitepackages() plus the user site of this interpreter. Best effort."""
    import site

    out: list[Path] = []
    seen: set[str] = set()

    def add(raw: object) -> None:
        if not raw:
            return
        path = Path(str(raw))
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        out.append(path)

    try:
        for item in site.getsitepackages() or []:
            add(item)
    except Exception:
        pass
    try:
        add(site.getusersitepackages())
    except Exception:
        pass
    user = getattr(site, "USER_SITE", None)
    if user:
        add(user)
    return out


def leftover_dirs(roots: list[Path] | None = None) -> list[Path]:
    """Tilde-renamed leftover `claimidx` directories in the given site-packages roots."""
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots if roots is not None else site_package_dirs():
        try:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if not child.is_dir() or not _LEFTOVER.search(child.name):
                    continue
                key = str(child)
                if key in seen:
                    continue
                seen.add(key)
                found.append(child)
        except OSError:
            continue
    return found


def leftover_report(roots: list[Path] | None = None) -> dict[str, Any]:
    """Additive doctor payload: ok, paths, and the recovery fix."""
    paths = [str(p) for p in leftover_dirs(roots)]
    ok = not paths
    rec: dict[str, Any] = {"ok": ok, "paths": paths}
    if ok:
        rec["fix"] = ""
        rec["detail"] = "no pip leftover dirs in site-packages"
    else:
        rec["fix"] = leftover_fix(paths)
        rec["detail"] = rec["fix"]
    return rec


def _looks_like_mcp(name: str) -> bool:
    text = (name or "").lower()
    if "pgrep" in text or "tasklist" in text:
        return False
    return "claimidx-mcp" in text or "claimidx.mcp_server" in text


def list_mcp_processes() -> list[dict[str, str]]:
    """Best-effort list of running claimidx-mcp processes. Never raises."""
    try:
        if os.name == "nt":
            return _list_mcp_windows()
        found = _list_mcp_procfs()
        if found is not None:
            return found
        return _list_mcp_pgrep()
    except Exception:
        return []


def mcp_is_running(processes: list[dict[str, str]] | None = None) -> bool:
    rows = processes if processes is not None else list_mcp_processes()
    return bool(rows)


def _list_mcp_windows() -> list[dict[str, str]]:
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    out: list[dict[str, str]] = []
    for raw in (proc.stdout or "").splitlines():
        line = raw.strip().strip('"')
        if not line:
            continue
        # CSV: "Image Name","PID","Session Name","Session#","Mem Usage"
        name = line.split('","', 1)[0].strip('"')
        if _looks_like_mcp(name):
            out.append({"name": name, "line": line})
    return out


def _list_mcp_procfs() -> list[dict[str, str]] | None:
    root = Path("/proc")
    if not root.is_dir():
        return None
    # macOS has no Linux procfs; a non-numeric-only /proc should still be scanned
    # only when pid directories with cmdline exist.
    found: list[dict[str, str]] = []
    saw_pid = False
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        saw_pid = True
        try:
            comm = (entry / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            comm = ""
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        except OSError:
            cmdline = ""
        blob = f"{comm} {cmdline}".strip()
        if blob and _looks_like_mcp(blob):
            found.append({"name": comm or cmdline, "pid": entry.name, "line": blob})
    if not saw_pid:
        return None
    return found


def _list_mcp_pgrep() -> list[dict[str, str]]:
    for args in (["pgrep", "-fl", "claimidx-mcp"], ["pgrep", "-f", "claimidx-mcp"]):
        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode not in (0, 1):
            continue
        out: list[dict[str, str]] = []
        for raw in (proc.stdout or "").splitlines():
            line = raw.strip()
            if not line or _looks_like_mcp(line) is False:
                continue
            # pgrep -f can match its own argv; skip those.
            if "pgrep" in line.lower():
                continue
            if _looks_like_mcp(line) or line.split()[:1] and line.split()[0].isdigit():
                # -f with no -l may be just a pid; confirm via /proc if we can, else keep
                if line.isdigit():
                    out.append({"name": "claimidx-mcp", "pid": line, "line": line})
                elif _looks_like_mcp(line):
                    out.append({"name": line, "line": line})
        return out
    return []


def mcp_running_message() -> str:
    return (
        "error: claimidx-mcp is running, so an upgrade can leave a half-uninstalled "
        f"package (WinError 32). Quit {_CLIENTS} or stop claimidx-mcp, then run "
        "claimidx upgrade again."
    )


def windows_print_only_message(command: str) -> str:
    return (
        "claimidx-mcp is not running. To upgrade, run this from a normal shell after "
        "this command exits (do not run pip from inside claimidx.exe; that can lock "
        "the executable on Windows):\n"
        f"{command}"
    )


def run_upgrade(
    *,
    yes: bool = False,
    dry_run: bool = False,
    windows: bool | None = None,
    processes: list[dict[str, str]] | None = None,
    executable: str | None = None,
) -> dict[str, Any]:
    """Plan or run an upgrade. Never spawns pip on Windows. Never raises for process listing."""
    win = os.name == "nt" if windows is None else bool(windows)
    running = mcp_is_running(processes)
    command = format_pip_upgrade_command(executable)
    argv = pip_upgrade_command(executable)
    result: dict[str, Any] = {
        "command": command,
        "argv": argv,
        "windows": win,
        "mcp_running": running,
        "dry_run": dry_run,
        "ran_pip": False,
        "rc": 0,
        "stdout": "",
        "stderr": "",
    }
    if win and running and not dry_run:
        result["rc"] = MCP_RUNNING_EXIT
        result["stderr"] = mcp_running_message()
        result["stdout"] = result["stderr"]
        return result
    if dry_run or win or not yes:
        lines = []
        if running:
            lines.append(
                f"warning: claimidx-mcp appears to be running. Quit {_CLIENTS} or stop claimidx-mcp before upgrading, or pip may leave leftover directories."
            )
        if win:
            lines.append(windows_print_only_message(command) if not running else f"Would run (after claimidx-mcp stops):\n{command}")
        else:
            lines.append(command if (dry_run or not yes) else command)
            if not dry_run and not yes:
                lines.append("Re-run with --yes to execute this command.")
        result["stdout"] = "\n".join(lines)
        result["rc"] = 0
        return result
    # POSIX + --yes + not dry-run: spawn pip in a child of this interpreter.
    try:
        proc = subprocess.run(argv, check=False)
        result["ran_pip"] = True
        result["rc"] = int(proc.returncode)
    except OSError as e:
        result["ran_pip"] = False
        result["rc"] = 2
        result["stderr"] = f"error: failed to start pip: {e}"
    return result
