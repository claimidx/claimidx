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


def test_mcp_run_schema_has_timeout():
    tool = next(t for t in TOOLS if t["name"] == "claimidx_run")
    assert "timeout" in tool["inputSchema"]["properties"]


def test_mcp_run_timeout_kills_a_hung_command(tmp_path):
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "claimidx_run",
                "arguments": {
                    "argv": [sys.executable, "-c", "import time; time.sleep(8)"],
                    "cwd": str(tmp_path),
                    "timeout": 0.4,
                },
            },
        },
        store,
    )
    assert rec.get("result", {}).get("isError") is not True
    body = json.loads(rec["result"]["content"][0]["text"])
    assert body["rc"] == 124


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
    assert "python -m claimidx run" in yml
    assert "python -m claimidx init" in yml
    assert "--no-hooks" in yml
    assert "--offline" not in yml
    assert "inputs:" in yml and "run:" in yml


def test_gemini_after_tool_failure_asks(tmp_path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    payload = json.dumps(
        {
            "hook_event_name": "AfterTool",
            "tool_name": "run_shell_command",
            "tool_input": {"command": "npx tsc --noEmit"},
            "tool_response": "TypeError: params is a Promise\n",
        }
    )
    rc = main(["--db", db, "hook", "--eco", "npm", "--dep", "next@15.0.0", "--err", payload])
    out = capsys.readouterr().out
    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "spr_a11c000000000001" in ctx
    assert "CLAIMIDX verdict" in ctx


def test_gemini_after_agent_does_not_block(tmp_path, capsys):
    """Gemini AfterAgent is not Claude Stop: inject a reminder, never decision:block."""
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "t"
    tree.mkdir()
    fail = json.dumps(
        {
            "hook_event_name": "AfterTool",
            "tool_name": "run_shell_command",
            "tool_input": {"command": "python app.py"},
            "cwd": str(tree),
            "tool_response": {"llmContent": "ModuleNotFoundError: No module named 'json'\n"},
        }
    )
    assert main(["--db", db, "hook", "--err", fail]) == 0
    capsys.readouterr()
    ok = json.dumps(
        {
            "hook_event_name": "AfterTool",
            "tool_name": "run_shell_command",
            "tool_input": {"command": "python app.py"},
            "cwd": str(tree),
            "tool_response": {"llmContent": "ok\n"},
            "exitCode": 0,
        }
    )
    assert main(["--db", db, "hook", "--err", ok]) == 0
    assert "CLAIMIDX fixed" in capsys.readouterr().out
    assert main(["--db", db, "hook", "--err", json.dumps({"hook_event_name": "AfterAgent"})]) == 0
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body.get("decision") != "block"
    ctx = (body.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    assert "claimidx claim --yes" in ctx


def test_gemini_llmcontent_list_and_type_field_ask(tmp_path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    payload = json.dumps(
        {
            "type": "AfterTool",
            "tool_name": "run_shell_command",
            "tool_args": {"command": "npx tsc --noEmit"},
            "tool_response": {
                "llmContent": ["TypeError: params is a Promise\n"],
                "error": {"message": "TypeError: params is a Promise"},
            },
        }
    )
    rc = main(["--db", db, "hook", "--eco", "npm", "--dep", "next@15.0.0", "--err", payload])
    out = capsys.readouterr().out
    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "spr_a11c000000000001" in ctx


def test_gemini_after_tool_object_payload_asks(tmp_path, capsys):
    """Gemini AfterTool sends tool_response as {llmContent, error}, not a string."""
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    payload = json.dumps(
        {
            "hook_event_name": "AfterTool",
            "cwd": "/tmp/app",
            "tool_name": "run_shell_command",
            "tool_args": {"command": "npx tsc --noEmit"},
            "tool_response": {
                "llmContent": "TypeError: params is a Promise\n",
                "returnDisplay": "failed",
                "error": {"type": "SHELL_ERROR", "message": "TypeError: params is a Promise"},
            },
        }
    )
    rc = main(["--db", db, "hook", "--eco", "npm", "--dep", "next@15.0.0", "--err", payload])
    out = capsys.readouterr().out
    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "spr_a11c000000000001" in ctx
    assert "CLAIMIDX verdict" in ctx


def test_gemini_hooks_are_portable_on_windows(tmp_path):
    from claimidx.hook import hook_command, install_gemini_hooks

    rec = install_gemini_hooks(tmp_path / "settings.json")
    assert rec["status"] == "installed"
    data = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    after = data["hooks"]["AfterTool"][0]["hooks"][0]
    assert "shell" not in after
    cmd = after["command"]
    assert "claimidx hook" in cmd
    assert not cmd.lstrip().startswith("&")
    # cmd.exe and PowerShell both accept a quoted interpreter.
    assert "-m claimidx hook" in hook_command()


def test_init_wires_codex_gemini_and_other_harnesses(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    for key, rel in (
        ("CLAIMIDX_CODEX_CONFIG", "codex/config.toml"),
        ("CLAIMIDX_GEMINI_CONFIG", "gemini/settings.json"),
        ("CLAIMIDX_CLINE_MCP", "cline/data/settings/cline_mcp_settings.json"),
        ("CLAIMIDX_CONTINUE_MCP", "continue/mcpServers/claimidx.json"),
        ("CLAIMIDX_WINDSURF_MCP", "codeium/windsurf/mcp_config.json"),
        ("CLAIMIDX_OPENCODE_CONFIG", "opencode/opencode.json"),
    ):
        path = tmp_path / rel
        monkeypatch.setenv(key, str(path))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".toml":
            path.write_text('model = "gpt-5"\n', encoding="utf-8")
        elif path.name == "opencode.json":
            path.write_text("{}\n", encoding="utf-8")
        elif "mcpServers" in str(path):
            pass
        else:
            path.write_text("{}\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "wiretest", "--offline"]) == 0
    out = json.loads(capsys.readouterr().out)
    harness = out["harness"]
    for key in ("codex", "codex_hooks", "gemini", "gemini_hooks", "cline", "continue", "windsurf"):
        assert harness[key]["status"] in {"installed", "updated"}, key
    assert "claimidx-mcp" in (tmp_path / "codex" / "config.toml").read_text(encoding="utf-8")
    gemini = json.loads((tmp_path / "gemini" / "settings.json").read_text(encoding="utf-8"))
    assert gemini["mcpServers"]["claimidx"]["command"] == "claimidx-mcp"
    assert "AfterTool" in json.dumps(gemini.get("hooks") or {})
    assert "claimidx hook" in json.dumps(gemini)
    codex_hooks = json.loads((tmp_path / "codex" / "hooks.json").read_text(encoding="utf-8"))
    assert "PostToolUse" in json.dumps(codex_hooks) and "claimidx hook" in json.dumps(codex_hooks)
    skill = (tmp_path / "gemini" / "skills" / "claimidx" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: claimidx" in skill
    assert (tmp_path / "codex" / "skills" / "claimidx" / "SKILL.md").is_file()
    cline = json.loads((tmp_path / "cline" / "data" / "settings" / "cline_mcp_settings.json").read_text(encoding="utf-8"))
    assert cline["mcpServers"]["claimidx"]["command"] == "claimidx-mcp"
    wind = json.loads((tmp_path / "codeium" / "windsurf" / "mcp_config.json").read_text(encoding="utf-8"))
    assert wind["mcpServers"]["claimidx"]["command"] == "claimidx-mcp"
    cont = json.loads((tmp_path / "continue" / "mcpServers" / "claimidx.json").read_text(encoding="utf-8"))
    assert "claimidx-mcp" in json.dumps(cont)
    assert main(["--db", db, "init", "--agent", "wiretest", "--offline"]) == 0
    again = json.loads(capsys.readouterr().out)["harness"]
    assert again["codex"]["status"] == "present"
    assert again["gemini"]["status"] == "present"


def test_codex_hooks_update_stale_matcher(tmp_path):
    from claimidx.hook import hook_command, install_codex_hooks

    cmd = hook_command()
    hooks = tmp_path / "hooks.json"
    hooks.write_text(
        json.dumps(
            {
                "hooks": {
                    "PostToolUseFailure": [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]}],
                    "PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": cmd}]}],
                    "SessionStart": [{"hooks": [{"type": "command", "command": cmd}]}],
                    "Stop": [{"hooks": [{"type": "command", "command": cmd}]}],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rec = install_codex_hooks(hooks)
    assert rec["status"] in {"installed", "updated"}
    data = json.loads(hooks.read_text(encoding="utf-8"))
    assert "shell" in (data["hooks"]["PostToolUse"][0].get("matcher") or "")


def test_subagent_stop_does_not_block(tmp_path, capsys):
    """SubagentStop is not Claude Stop: remind, never decision:block."""
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "t"
    tree.mkdir()
    fail = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "python app.py"},
            "cwd": str(tree),
            "tool_response": {"stderr": "ModuleNotFoundError: No module named 'json'"},
        }
    )
    assert main(["--db", db, "hook", "--err", fail]) == 0
    capsys.readouterr()
    ok = json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python app.py"},
            "cwd": str(tree),
            "tool_response": {"stdout": "ok\n"},
        }
    )
    assert main(["--db", db, "hook", "--err", ok]) == 0
    assert "CLAIMIDX fixed" in capsys.readouterr().out
    assert main(["--db", db, "hook", "--err", json.dumps({"hook_event_name": "SubagentStop"})]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body.get("decision") != "block"
    ctx = (body.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    assert "claimidx claim --yes" in ctx


def test_cursor_hooks_restore_missing_post_tool_use_failure(tmp_path):
    from claimidx.hook import hook_command, install_cursor_hooks

    cmd = hook_command()
    path = tmp_path / "hooks.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "afterShellExecution": [{"command": cmd, "timeout": 15}],
                    "postToolUse": [{"command": cmd, "timeout": 15}],
                    "sessionStart": [{"command": cmd, "timeout": 15}],
                    "stop": [{"command": cmd, "timeout": 15, "loop_limit": 1}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rec = install_cursor_hooks(path)
    assert rec["status"] in {"installed", "updated"}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert any("claimidx hook" in json.dumps(h) for h in data["hooks"].get("postToolUseFailure") or [])


def test_gemini_after_tool_success_without_exit_does_not_ask(tmp_path, capsys):
    """Gemini AfterTool often omits exitCode; success output must not look like a failure."""
    db = str(tmp_path / "ix.sqlite")
    payload = json.dumps(
        {
            "hook_event_name": "AfterTool",
            "tool_name": "run_shell_command",
            "tool_args": {"command": "echo hello"},
            "tool_response": {
                "llmContent": "hello from a successful shell command\n",
                "returnDisplay": "hello from a successful shell command",
            },
        }
    )
    assert main(["--db", db, "hook", "--err", payload]) == 0
    assert "CLAIMIDX verdict" not in capsys.readouterr().out


def test_grok_hooks_update_stale_matcher(tmp_path):
    from claimidx.hook import grok_hook_file, hook_command, install_grok_hooks

    cmd = hook_command()
    path = tmp_path / "claimidx.json"
    payload = grok_hook_file(cmd)
    payload["hooks"]["PostToolUse"][0]["matcher"] = "Bash"
    payload["hooks"]["PostToolUseFailure"][0]["matcher"] = "Bash"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    rec = install_grok_hooks(path)
    assert rec["status"] in {"installed", "updated"}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "run_terminal_command" in (data["hooks"]["PostToolUse"][0].get("matcher") or "")
    assert "run_terminal_command" in (data["hooks"]["PostToolUseFailure"][0].get("matcher") or "")


def test_flat_mcp_override_keeps_skill_next_to_the_file(tmp_path, capsys, monkeypatch):
    """A CLAIMIDX_*_MCP file that is not nested under data/settings must not walk to grandparents."""
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CLAIMIDX_CLINE_MCP", str(tmp_path / "cline.json"))
    monkeypatch.setenv("CLAIMIDX_CONTINUE_MCP", str(tmp_path / "continue.json"))
    (tmp_path / "cline.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "continue.json").write_text("{}\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "wiretest", "--offline"]) == 0
    capsys.readouterr()
    leaked = tmp_path.parent / "skills" / "claimidx" / "SKILL.md"
    assert not leaked.exists()
    assert (tmp_path / "skills" / "claimidx" / "SKILL.md").is_file()
