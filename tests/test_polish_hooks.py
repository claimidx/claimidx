"""Polish gate: idempotent Claude install, one grouped-hooks helper family, safe Grok JSON load, packaged MCP resources, clamped limits."""

import json
from pathlib import Path

from claimidx.mcp_server import handle
from claimidx.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---- 1. install_claude_hook short-circuits when the settings are already current ------


def test_claude_hook_install_second_call_is_present(tmp_path):
    from claimidx.hook import install_claude_hook

    settings = tmp_path / "claude" / "settings.json"
    first = install_claude_hook(settings)
    assert first["status"] == "installed"
    before = _read(settings)
    stat = settings.stat()
    second = install_claude_hook(settings)
    assert second["status"] == "present"
    assert set(second) >= {"path", "command", "event", "events", "matcher", "status"}
    assert second["events"] == first["events"]
    assert _read(settings) == before
    assert settings.stat().st_mtime_ns == stat.st_mtime_ns


def test_claude_hook_install_present_keeps_foreign_hooks_untouched(tmp_path):
    from claimidx.hook import install_claude_hook

    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}) + "\n", encoding="utf-8")
    assert install_claude_hook(settings)["status"] == "installed"
    body = _read(settings)
    assert install_claude_hook(settings)["status"] == "present"
    assert _read(settings) == body
    data = json.loads(body)
    assert data["theme"] == "dark"
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo stop"


def test_claude_hook_install_rewrites_changed_command(tmp_path, monkeypatch):
    from claimidx import hook as hookmod

    settings = tmp_path / "claude" / "settings.json"
    real = hookmod.hook_command()
    monkeypatch.setattr(hookmod, "hook_command", lambda: "old-python -m claimidx hook")
    assert hookmod.install_claude_hook(settings)["status"] == "installed"
    assert "old-python -m claimidx hook" in _read(settings)
    monkeypatch.setattr(hookmod, "hook_command", lambda: real)
    rec = hookmod.install_claude_hook(settings)
    assert rec["status"] == "installed"
    assert rec["command"] == real
    text = _read(settings)
    assert "old-python" not in text
    commands = {h["command"] for groups in json.loads(text)["hooks"].values() for g in groups for h in g["hooks"]}
    assert commands == {real}
    assert hookmod.install_claude_hook(settings)["status"] == "present"


def test_claude_hook_install_via_cli_returns_present(tmp_path, capsys):
    from claimidx.cli import main

    assert main(["hook", "--install"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "installed"
    assert main(["hook", "--install"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "present"


# ---- 2. one grouped-hooks helper family; Grok events defined once ---------------------


def test_grok_events_defined_once_and_drive_the_hook_file():
    from claimidx.hook import GROK_EVENTS, grok_hook_file

    assert [e for e, _m in GROK_EVENTS] == ["PostToolUseFailure", "PostToolUse", "SessionStart", "Stop"]
    payload = grok_hook_file("x -m claimidx hook")
    for event, matcher in GROK_EVENTS:
        group = payload["hooks"][event][0]
        assert group.get("matcher") == matcher
        assert group["hooks"][0]["command"] == "x -m claimidx hook"


def test_claude_and_grok_checks_are_the_grouped_checks():
    from claimidx.hook import (
        CLAUDE_EVENTS,
        GROK_EVENTS,
        grok_hook_file,
        grok_hooks_current,
        grok_hooks_has_claimidx,
        grouped_hooks_current,
        grouped_hooks_has_claimidx,
        merge_claude_hooks,
        settings_has_claimidx,
    )

    cmd = "x -m claimidx hook"
    grok = grok_hook_file(cmd)
    assert grok_hooks_has_claimidx(grok) is True
    assert grok_hooks_current(grok, cmd) is True
    assert grok_hooks_current(grok, "y -m claimidx hook") is False
    assert grouped_hooks_has_claimidx(grok, GROK_EVENTS) is True
    assert grouped_hooks_current(grok, GROK_EVENTS, cmd) is True
    # The Grok matcher differs from Claude's, so the same file is not Claude-current.
    assert grouped_hooks_current(grok, CLAUDE_EVENTS, cmd) is False
    claude = merge_claude_hooks({})
    assert settings_has_claimidx(claude) is True
    assert grouped_hooks_has_claimidx(claude, CLAUDE_EVENTS) is True
    assert settings_has_claimidx({"hooks": {"Stop": []}}) is False
    assert settings_has_claimidx({"hooks": ["Stop"]}) is False
    assert grok_hooks_has_claimidx({"hooks": ["Stop"]}) is False
    # A partial file (one event missing) is not present.
    partial = json.loads(json.dumps(grok))
    del partial["hooks"]["Stop"]
    assert grok_hooks_has_claimidx(partial) is False
    assert grok_hooks_current(partial, cmd) is False


# ---- 3. install_grok_hooks refuses to clobber a non-object claimidx.json ----------------


def test_grok_hooks_refuses_non_object(tmp_path, monkeypatch):
    from claimidx.hook import install_grok_hooks

    hooks = tmp_path / "grok" / "hooks" / "claimidx.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("[]\n", encoding="utf-8")
    rec = install_grok_hooks(hooks)
    assert rec["status"] == "error"
    assert "not a json object" in rec["error"]
    assert _read(hooks) == "[]\n"


def test_grok_hooks_refuses_bad_json(tmp_path):
    from claimidx.hook import install_grok_hooks

    hooks = tmp_path / "grok" / "hooks" / "claimidx.json"
    hooks.parent.mkdir(parents=True)
    hooks.write_text("{not json", encoding="utf-8")
    rec = install_grok_hooks(hooks)
    assert rec["status"] == "error"
    assert _read(hooks) == "{not json"


def test_grok_hooks_install_then_present(tmp_path):
    from claimidx.hook import install_grok_hooks

    hooks = tmp_path / "grok" / "hooks" / "claimidx.json"
    assert install_grok_hooks(hooks)["status"] == "installed"
    body = _read(hooks)
    assert install_grok_hooks(hooks)["status"] == "present"
    assert _read(hooks) == body


# ---- 4. MCP resources answer from the wheel, not only the checkout ---------------------


def _resource_text(store, uri: str) -> str:
    rec = handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {"uri": uri}}, store)
    assert "error" not in rec, rec
    return rec["result"]["contents"][0]["text"]


def test_mcp_resources_survive_missing_checkout(tmp_path, monkeypatch):
    from claimidx import mcp_server

    monkeypatch.setattr(mcp_server, "_ROOT", tmp_path / "absent")
    store = Store(tmp_path / "ix.sqlite")
    skill = _resource_text(store, "claimidx://skill")
    assert "claimidx" in skill.lower() and "ingest" in skill.lower()
    agents = _resource_text(store, "claimidx://agents")
    assert "claimidx" in agents.lower()
    protocol = _resource_text(store, "claimidx://protocol")
    assert "claimidx" in protocol.lower()
    # The fallbacks are the real documents, kept in sync by scripts/sync_docs.py.
    assert skill == _read(ROOT / "skills" / "claimidx" / "SKILL.md").replace("\r\n", "\n")
    assert agents == _read(ROOT / "AGENTS.md").replace("\r\n", "\n")
    assert protocol == _read(ROOT / "PROTOCOL.md").replace("\r\n", "\n")
    unknown = handle({"jsonrpc": "2.0", "id": 2, "method": "resources/read", "params": {"uri": "claimidx://nope"}}, store)
    assert "error" in unknown


def test_bundled_skill_prefers_the_checkout():
    """The checkout candidate must be <repo>/skills/claimidx/SKILL.md, not a path one level above the repo."""
    from claimidx.hook import bundled_skill_candidates

    candidates = bundled_skill_candidates()
    assert candidates[0] == ROOT / "skills" / "claimidx" / "SKILL.md"
    assert candidates[-1] == ROOT / "src" / "claimidx" / "data" / "SKILL.md"


def test_mcp_resource_docs_are_packaged():
    data = ROOT / "src" / "claimidx" / "data"
    for name in ("SKILL.md", "AGENTS.md", "PROTOCOL.md"):
        assert (data / name).is_file(), name
    pyproject = _read(ROOT / "pyproject.toml")
    for name in ("data/SKILL.md", "data/AGENTS.md", "data/PROTOCOL.md"):
        assert name in pyproject, name


# ---- 5. claimidx_leaderboard clamps limit to the documented max ------------------------


def _leaderboard_limit(store, limit, monkeypatch):
    import claimidx.board as board

    seen: dict = {}

    def fake(*, days: int = 30, limit: int = 50, own: str = ""):
        seen.update(days=days, limit=limit, own=own)
        return {"days": days, "rules": [], "authors": [], "verifiers": []}

    monkeypatch.setattr(board, "fetch_leaderboard", fake)
    args: dict = {}
    if limit is not None:
        args["limit"] = limit
    rec = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claimidx_leaderboard", "arguments": args}}, store)
    assert rec.get("result", {}).get("isError") is not True, rec
    return seen["limit"]


def test_leaderboard_limit_is_clamped(tmp_path, monkeypatch):
    from claimidx.mcp_server import TOOLS

    tool = next(t for t in TOOLS if t["name"] == "claimidx_leaderboard")
    assert "max 200" in tool["inputSchema"]["properties"]["limit"]["description"]
    store = Store(tmp_path / "ix.sqlite")
    assert _leaderboard_limit(store, 999, monkeypatch) == 200
    assert _leaderboard_limit(store, 200, monkeypatch) == 200
    assert _leaderboard_limit(store, 7, monkeypatch) == 7
    assert _leaderboard_limit(store, None, monkeypatch) == 25
    assert _leaderboard_limit(store, 0, monkeypatch) == 25
    assert _leaderboard_limit(store, -5, monkeypatch) == 1
