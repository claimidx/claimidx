"""Harness presence: user skill drop, Cursor hooks, MCP run/core, CI action, Grok session brief."""

from __future__ import annotations

import json
import sys
from io import StringIO

from claimidx.cli import main
from claimidx.mcp_server import CORE_TOOL_NAMES, TOOLS, handle, listed_tools


def test_core_tool_set_is_the_loop_not_the_catalog():
    names = {t["name"] for t in TOOLS}
    assert "claimidx_run" in names
    core = CORE_TOOL_NAMES
    assert core <= names
    assert core == {
        "claimidx_ask",
        "claimidx_claim",
        "claimidx_apply",
        "claimidx_run",
        "claimidx_hook",
        "claimidx_whoami",
        "claimidx_doctor",
    }
    assert {t["name"] for t in listed_tools()} == names


def test_listed_tools_core_env(monkeypatch):
    monkeypatch.setenv("CLAIMIDX_MCP_TOOLS", "core")
    listed = {t["name"] for t in listed_tools()}
    assert listed == CORE_TOOL_NAMES
    monkeypatch.setenv("CLAIMIDX_MCP_TOOLS", "all")
    assert {t["name"] for t in listed_tools()} == {t["name"] for t in TOOLS}


def test_mcp_run_asks_on_failure_without_streaming_stdio(tmp_path):
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    app = tmp_path / "app.py"
    app.write_text("import no_such_mod_cix_mcp_run\n", encoding="utf-8")
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "claimidx_run",
                "arguments": {"argv": [sys.executable, str(app)], "cwd": str(tmp_path)},
            },
        },
        store,
    )
    assert rec.get("result", {}).get("isError") is not True
    body = json.loads(rec["result"]["content"][0]["text"])
    assert body["rc"] != 0
    assert "ModuleNotFoundError" in (body.get("output") or "")
    assert "CLAIMIDX" in (body.get("advice") or "")
    assert "verdict" in body


def test_init_drops_user_skill_and_cursor_hooks(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    cursor = tmp_path / "cursor" / "mcp.json"
    grok = tmp_path / "grok" / "config.toml"
    monkeypatch.setenv("CLAIMIDX_CURSOR_MCP", str(cursor))
    monkeypatch.setenv("CLAIMIDX_GROK_CONFIG", str(grok))
    grok.parent.mkdir(parents=True)
    grok.write_text("[cli]\ntheme = 1\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "wiretest", "--offline"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["harness"]["cursor_hooks"]["status"] == "installed"
    assert out["harness"]["skills"]["claude"]["status"] == "installed"
    assert out["harness"]["skills"]["grok"]["status"] == "installed"
    assert out["harness"]["skills"]["cursor"]["status"] == "installed"
    skill = (tmp_path / "claude" / "skills" / "claimidx" / "SKILL.md").read_text(encoding="utf-8")
    assert skill.startswith("---") and "name: claimidx" in skill
    grok_skill = (tmp_path / "grok" / "skills" / "claimidx" / "SKILL.md").read_text(encoding="utf-8")
    assert "claimidx claim --yes" in grok_skill
    hooks = json.loads((tmp_path / "cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert hooks["version"] == 1
    blob = json.dumps(hooks)
    assert "afterShellExecution" in blob and "claimidx hook" in blob
    assert main(["--db", db, "init", "--agent", "wiretest", "--offline"]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["harness"]["cursor_hooks"]["status"] == "present"
    assert again["harness"]["skills"]["claude"]["status"] == "present"


def test_cursor_after_shell_failure_asks(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    payload = json.dumps(
        {
            "hook_event_name": "afterShellExecution",
            "command": "npx tsc --noEmit",
            "output": "TypeError: params is a Promise\n",
            "duration": 12,
        }
    )
    monkeypatch.setattr("sys.stdin", StringIO(payload))
    rc = main(["--db", db, "hook", "--eco", "npm", "--dep", "next@15.0.0"])
    out = capsys.readouterr().out
    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "spr_a11c000000000001" in ctx
    assert "CLAIMIDX verdict" in ctx


def test_grok_session_start_brief_lands_on_first_tool_event(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setenv("GROK_HOOK_EVENT", "session_start")
    assert main(["--db", db, "hook", "--err", json.dumps({"hook_event_name": "SessionStart"})]) == 0
    assert capsys.readouterr().out == ""
    monkeypatch.setenv("GROK_HOOK_EVENT", "post_tool_use")
    ok = json.dumps(
        {
            "hookEventName": "post_tool_use",
            "hook_event_name": "PostToolUse",
            "toolName": "run_terminal_command",
            "toolInput": {"command": "echo hi"},
            "toolResult": {"type": "Bash", "command": "echo hi", "exit_code": 0, "output_for_prompt": "hi\n"},
        }
    )
    assert main(["--db", db, "hook", "--err", ok]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("CLAIMIDX 7d:")
    assert main(["--db", db, "hook", "--err", ok]) == 0
    assert capsys.readouterr().out == ""


def test_github_action_wraps_claimidx_run():
    from claimidx.discovery import ROOT

    yml = (ROOT / ".github" / "actions" / "run" / "action.yml").read_text(encoding="utf-8")
    assert "claimidx run" in yml
    assert "inputs:" in yml and "run:" in yml
