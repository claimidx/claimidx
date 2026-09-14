import json
from io import StringIO

from claimidx.cli import main
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.hook import extract_hook_err
from claimidx.mcp_server import handle
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def test_extract_raw_error_line():
    err, event = extract_hook_err("ModuleNotFoundError: No module named 'cgi'\n")
    assert err and "cgi" in err
    assert event is None


def test_extract_tool_result_field():
    raw = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_result": "ImportError: No module named 'cgi'\n",
        }
    )
    err, event = extract_hook_err(raw)
    assert event == "PostToolUseFailure"
    assert err and "cgi" in err


def test_extract_claude_failure_json():
    raw = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_response": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'cgi'\n",
        }
    )
    err, event = extract_hook_err(raw)
    assert event == "PostToolUseFailure"
    assert err and "cgi" in err


def test_extract_skips_successful_posttooluse():
    raw = json.dumps(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_response": "ok\n",
        }
    )
    err, event = extract_hook_err(raw)
    assert err is None
    assert event == "PostToolUse"


def test_extract_refuses_secret_shaped_stderr():
    raw = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_response": "error: Bearer supersecrettokenvalue123456\n",
        }
    )
    err, event = extract_hook_err(raw)
    assert err is None
    assert event == "PostToolUseFailure"


def test_hook_cli_hit_and_claude_context(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_response": "TypeError: params is a Promise\n",
        }
    )
    monkeypatch.setattr("sys.stdin", StringIO(payload))
    rc = main(["--db", db, "hook", "--eco", "npm", "--dep", "next@15.0.0"])
    out = capsys.readouterr().out
    assert rc == 0
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "spr_a11c000000000001" in ctx
    assert "Do not execute fix.b" in ctx


def test_hook_cli_near_tie_surfaces_both(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    err = "TypeError: params is a Promise"
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
                "npm",
                "--rt",
                "node@18",
                "--dep",
                "next@15.0.0",
                "--fix-k",
                "patch",
                "--fix-b",
                "await params",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    a = capsys.readouterr().out.strip()
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
                "npm",
                "--rt",
                "node@20",
                "--dep",
                "next@15.0.0",
                "--fix-k",
                "patch",
                "--fix-b",
                "await searchParams",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    b = capsys.readouterr().out.strip()
    assert a != b
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_response": err + "\n",
        }
    )
    monkeypatch.setattr("sys.stdin", StringIO(payload))
    rc = main(["--db", db, "hook", "--eco", "npm", "--dep", "next@15.0.0"])
    out = capsys.readouterr().out
    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "near-tie" in ctx
    assert "await params" in ctx and "await searchParams" in ctx
    assert "Do not execute fix.b" in ctx
    assert a in ctx and b in ctx


def test_hook_install_refuses_to_wipe_bad_json(tmp_path, capsys, monkeypatch):
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json", encoding="utf-8")
    rc = main(["hook", "--install"])
    assert rc == 2
    assert settings.read_text(encoding="utf-8") == "{not json"
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_hook_install_refuses_to_wipe_non_object(tmp_path, capsys, monkeypatch):
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("[]\n", encoding="utf-8")
    rc = main(["hook", "--install"])
    assert rc == 2
    assert settings.read_text(encoding="utf-8") == "[]\n"
    assert json.loads(capsys.readouterr().out)["status"] == "error"


def test_hook_install_refuses_hooks_array(tmp_path, capsys, monkeypatch):
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    raw = json.dumps({"theme": "dark", "hooks": ["Stop"]}) + "\n"
    settings.write_text(raw, encoding="utf-8")
    rc = main(["hook", "--install"])
    assert rc == 2
    assert settings.read_text(encoding="utf-8") == raw
    assert "not an object" in json.loads(capsys.readouterr().out)["error"]


def test_hook_command_invokes_on_windows():
    import os
    import sys
    from claimidx.hook import claude_hook_block, hook_command

    if os.name != "nt":
        return
    cmd = hook_command()
    assert "-m claimidx hook" in cmd
    assert sys.executable in cmd or cmd.startswith('"')
    assert not cmd.lstrip().startswith("&")
    assert claude_hook_block()["hooks"][0].get("shell") == "powershell"


def test_cursor_mcp_refuses_non_object(tmp_path):
    from claimidx.hook import install_cursor_mcp

    path = tmp_path / "mcp.json"
    path.write_text("[]\n", encoding="utf-8")
    rec = install_cursor_mcp(path, own="did:claimidx:test")
    assert rec["status"] == "error"
    assert path.read_text(encoding="utf-8") == "[]\n"


def test_grok_mcp_appends_without_clobber(tmp_path, monkeypatch):
    from claimidx.hook import install_grok_mcp

    monkeypatch.setenv("CLAIMIDX_GROK_CONFIG", str(tmp_path / "config.toml"))
    path = tmp_path / "config.toml"
    path.write_text("[ui]\nfoo = 1\n", encoding="utf-8")
    rec = install_grok_mcp(path, own="did:claimidx:test", agent="test")
    assert rec["status"] == "installed"
    text = path.read_text(encoding="utf-8")
    assert "[ui]" in text and "foo = 1" in text
    assert "[mcp_servers.claimidx]" in text
    rec2 = install_grok_mcp(path, own="did:claimidx:other")
    assert rec2["status"] == "updated"
    text2 = path.read_text(encoding="utf-8")
    assert "[ui]" in text2 and "foo = 1" in text2
    assert "did:claimidx:other" in text2
    assert "did:claimidx:test" not in text2


def test_hook_install_merges_without_clobber(tmp_path, capsys, monkeypatch):
    settings = tmp_path / "claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}) + "\n", encoding="utf-8")
    rc = main(["hook", "--install"])
    assert rc == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo stop"
    fail = data["hooks"]["PostToolUseFailure"]
    assert any("claimidx hook" in json.dumps(g) for g in fail)
    out = json.loads(capsys.readouterr().out)
    assert out["event"] == "PostToolUseFailure"
    rc2 = main(["hook", "--install"])
    assert rc2 == 0
    again = json.loads(settings.read_text(encoding="utf-8"))
    n = sum(1 for g in again["hooks"]["PostToolUseFailure"] if "claimidx hook" in json.dumps(g))
    assert n == 1


def test_init_no_hooks_skips_settings(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "init", "--agent", "agent-a", "--offline", "--no-hooks"])
    assert rc == 0
    assert not (tmp_path / "claude" / "settings.json").exists()


def test_hook_cli_miss_is_visible(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_response": "ModuleNotFoundError: No module named 'xyzzy_unknown_pkg'\n",
        }
    )
    monkeypatch.setattr("sys.stdin", StringIO(payload))
    rc = main(["--db", db, "hook", "--eco", "py"])
    out = capsys.readouterr().out
    assert rc == 0
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "CLAIMIDX miss" in ctx
    assert "hit 0" in ctx
    assert "xyzzy_unknown_pkg" in ctx or "fp " in ctx or "fp=" in ctx
    assert "Do not execute fix.b" not in ctx or "ingest" in ctx.lower() or "miss" in ctx


def test_hook_cli_no_err_stays_silent(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setattr("sys.stdin", StringIO(""))
    rc = main(["--db", db, "hook", "--eco", "py"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_hook_cli_raw_stderr_miss_prints_line(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setattr("sys.stdin", StringIO("ModuleNotFoundError: No module named 'xyzzy_unknown_pkg'\n"))
    rc = main(["--db", db, "hook", "--eco", "py"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.startswith("CLAIMIDX verdict solve")
    assert "CLAIMIDX miss" in out
    assert "hit 0" in out


def test_mcp_hook_is_evidence_only(tmp_path):
    store = Store(tmp_path / "ix.sqlite")
    err = "TypeError: params is a Promise"
    store.put(
        Claim(
            fp=fingerprint(err=err, cls="type_error", eco="npm", dep=["next@15.0.0"]),
            cls="type_error",
            err=normalize_error(err),
            eco="npm",
            dep=["next@15.0.0"],
            fix=Fix(k="patch", b="await params"),
            eval=EvalSpec(cmd="true"),
            own="did:claimidx:test",
        )
    )
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "claimidx_hook",
                "arguments": {
                    "raw": json.dumps(
                        {
                            "hook_event_name": "PostToolUseFailure",
                            "tool_response": err + "\n",
                        }
                    ),
                    "eco": "npm",
                    "dep": ["next@15.0.0"],
                },
            },
        },
        store,
    )
    assert rec.get("result", {}).get("isError") is not True
    body = json.loads(rec["result"]["content"][0]["text"])
    assert body["hit"] is True
    assert body["apply_fix"] is False
    assert "Do not execute fix.b" in (body.get("note") or "")
    assert body["claims"]
    assert "await params" in json.dumps(body)


def test_mcp_hook_fail_open_empty_and_secrets(tmp_path):
    store = Store(tmp_path / "ix.sqlite")
    empty = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "claimidx_hook", "arguments": {}}},
        store,
    )
    assert empty.get("result", {}).get("isError") is not True
    empty_body = json.loads(empty["result"]["content"][0]["text"])
    assert empty_body["hit"] is False
    assert empty_body["apply_fix"] is False
    secret = handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "claimidx_hook", "arguments": {"err": "error: Bearer supersecrettokenvalue123456"}},
        },
        store,
    )
    assert secret.get("result", {}).get("isError") is not True
    secret_body = json.loads(secret["result"]["content"][0]["text"])
    assert secret_body["hit"] is False
    assert secret_body["apply_fix"] is False


def test_install_wires_all_four_events(tmp_path, capsys):
    settings = tmp_path / "claude" / "settings.json"
    assert main(["hook", "--install"]) == 0
    data = json.loads(settings.read_text(encoding="utf-8"))
    for event in ("PostToolUseFailure", "PostToolUse", "SessionStart", "Stop"):
        assert any("claimidx hook" in json.dumps(g) for g in data["hooks"][event]), event
    assert data["hooks"]["PostToolUse"][0]["matcher"] == "Bash"
    assert "matcher" not in data["hooks"]["Stop"][0]
    assert main(["hook", "--install"]) == 0
    data2 = json.loads(settings.read_text(encoding="utf-8"))
    assert data2 == data  # idempotent


def test_never_forget_flow(tmp_path, capsys):
    """failure -> same command passes -> one nudge with the draft -> Stop blocks once -> claim clears it."""
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
            "tool_input": {"command": "python  app.py"},
            "cwd": str(tree),
            "tool_response": {"stdout": "ok\n"},
        }
    )
    assert main(["--db", db, "hook", "--err", ok]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("CLAIMIDX fixed: `python  app.py`") and "claimidx claim --yes" in ctx and 'eval=python -c "import json"' in ctx
    # Not twice.
    assert main(["--db", db, "hook", "--err", ok]) == 0
    assert capsys.readouterr().out == ""
    # Stop blocks once with the reason, then never again.
    assert main(["--db", db, "hook", "--err", json.dumps({"hook_event_name": "Stop"})]) == 0
    stop = json.loads(capsys.readouterr().out)
    assert stop["decision"] == "block" and "claimidx claim --yes" in stop["reason"]
    assert main(["--db", db, "hook", "--err", json.dumps({"hook_event_name": "Stop"})]) == 0
    assert capsys.readouterr().out == ""
    # Claiming consumes the remembered failure.
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--fix", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    from claimidx.env import last_failure

    assert last_failure() is None
    # A session brief is one line and never fails.
    assert main(["--db", db, "hook", "--err", json.dumps({"hook_event_name": "SessionStart"})]) == 0
    brief = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert brief.startswith("CLAIMIDX 7d:") and "claimidx claim --yes" in brief


def test_success_hook_stays_silent_without_a_remembered_failure(tmp_path, capsys):
    db = str(tmp_path / "ix.sqlite")
    ok = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}, "tool_response": {"stdout": "a\n"}})
    assert main(["--db", db, "hook", "--err", ok]) == 0
    assert capsys.readouterr().out == ""


def _grok_posttooluse(*, command: str, exit_code: int, output: str, cwd: str = "") -> str:
    """Grok fires PostToolUse for a failed shell (non-zero exit), with camelCase keys."""
    result = {
        "type": "Bash",
        "command": command,
        "exit_code": exit_code,
        "output_for_prompt": output,
    }
    payload = {
        "hookEventName": "post_tool_use",
        "hook_event_name": "PostToolUse",
        "toolName": "run_terminal_command",
        "toolInput": {"command": command},
        "toolResult": result,
        "tool_response": result,
    }
    if cwd:
        payload["cwd"] = cwd
        payload["workspaceRoot"] = cwd
    return json.dumps(payload)


def test_extract_grok_failed_shell_payload():
    from claimidx.hook import extract_hook_context, extract_hook_err, extract_hook_exit, posttooluse_is_failure

    raw = _grok_posttooluse(command="python app.py", exit_code=1, output="ModuleNotFoundError: No module named 'cgi'\n")
    err, event = extract_hook_err(raw)
    assert event == "PostToolUse"
    assert err and "cgi" in err
    ctx = extract_hook_context(raw)
    assert ctx.get("command") == "python app.py"
    assert "cgi" in (ctx.get("body") or "")
    assert extract_hook_exit(raw) == 1
    assert posttooluse_is_failure(raw) is True


def test_extract_grok_success_shell_is_not_a_failure():
    from claimidx.hook import extract_hook_err, extract_hook_exit, posttooluse_is_failure

    raw = _grok_posttooluse(command="python app.py", exit_code=0, output="ok\n")
    err, event = extract_hook_err(raw)
    assert event == "PostToolUse"
    assert err is None
    assert extract_hook_exit(raw) == 0
    assert posttooluse_is_failure(raw) is False


def test_hook_cli_grok_failed_posttooluse_asks(tmp_path, capsys, monkeypatch):
    """Grok PostToolUse + exit_code 1 must ask, not take the success-nudge path."""
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    payload = _grok_posttooluse(
        command="npx tsc --noEmit",
        exit_code=1,
        output="TypeError: params is a Promise\n",
    )
    monkeypatch.setattr("sys.stdin", StringIO(payload))
    rc = main(["--db", db, "hook", "--eco", "npm", "--dep", "next@15.0.0"])
    out = capsys.readouterr().out
    assert rc == 0
    body = json.loads(out)
    ctx = body["hookSpecificOutput"]["additionalContext"]
    assert "spr_a11c000000000001" in ctx
    assert "CLAIMIDX verdict" in ctx
    assert "Do not execute fix.b" in ctx


def test_hook_cli_grok_never_forget_flow(tmp_path, capsys):
    """Same never-forget loop, but with Grok's PostToolUse-for-failure envelope."""
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "t"
    tree.mkdir()
    fail = _grok_posttooluse(
        command="python app.py",
        exit_code=1,
        output="ModuleNotFoundError: No module named 'json'\n",
        cwd=str(tree),
    )
    assert main(["--db", db, "hook", "--err", fail]) == 0
    capsys.readouterr()
    ok = _grok_posttooluse(command="python app.py", exit_code=0, output="ok\n", cwd=str(tree))
    assert main(["--db", db, "hook", "--err", ok]) == 0
    ctx = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.startswith("CLAIMIDX fixed: `python app.py`") and "claimidx claim --yes" in ctx


def test_init_wires_grok_native_hooks(tmp_path, capsys, monkeypatch):
    import json as _json

    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    grok = tmp_path / "grok" / "config.toml"
    hooks = tmp_path / "grok" / "hooks" / "claimidx.json"
    monkeypatch.setenv("CLAIMIDX_GROK_CONFIG", str(grok))
    grok.parent.mkdir(parents=True)
    grok.write_text('[cli]\ntheme = "dark"\n', encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "init", "--agent", "wiretest", "--offline"])
    assert rc == 0
    out = _json.loads(capsys.readouterr().out)
    assert out["harness"]["grok_hooks"]["status"] == "installed"
    data = _json.loads(hooks.read_text(encoding="utf-8"))
    blob = _json.dumps(data)
    assert "PostToolUse" in blob and "PostToolUseFailure" in blob
    assert "claimidx hook" in blob
    assert "run_terminal_command" in blob
    rc2 = main(["--db", db, "init", "--agent", "wiretest", "--offline"])
    assert rc2 == 0
    again = _json.loads(capsys.readouterr().out)
    assert again["harness"]["grok_hooks"]["status"] == "present"


def test_hook_duplicate_failure_within_3s_is_silent(tmp_path, capsys):
    """Two identical failure payloads in the same second must not ask twice (hook double-fire)."""
    db = str(tmp_path / "ix.sqlite")
    fail = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "python app.py"},
            "tool_response": {"stderr": "ModuleNotFoundError: No module named 'json'"},
        }
    )
    assert main(["--db", db, "hook", "--err", fail]) == 0
    first = capsys.readouterr().out
    assert "CLAIMIDX" in first
    assert main(["--db", db, "hook", "--err", fail]) == 0
    assert capsys.readouterr().out == ""


def test_hook_fail_after_nudge_is_not_swallowed(tmp_path, capsys):
    """A success nudge must not 3s-debounce a later failure of the same err."""
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
    assert "CLAIMIDX" in capsys.readouterr().out
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
    assert main(["--db", db, "hook", "--err", fail]) == 0
    again = capsys.readouterr().out
    assert "CLAIMIDX" in again
    assert "verdict" in again or "miss" in again


def test_cursor_top_level_exit_zero_is_not_a_failure():
    from claimidx.hook import extract_hook_err, extract_hook_exit, hook_is_failure

    raw = json.dumps(
        {
            "hook_event_name": "afterShellExecution",
            "command": "npx tsc --noEmit",
            "output": "error: none\ncompiled ok\n",
            "exitCode": 0,
        }
    )
    err, event = extract_hook_err(raw)
    assert err and "error" in err.lower()
    assert event == "PostToolUse"
    assert extract_hook_exit(raw) == 0
    assert hook_is_failure(raw, event, err) is False


def test_cursor_after_shell_exit_zero_stays_silent(tmp_path, capsys):
    db = str(tmp_path / "ix.sqlite")
    payload = json.dumps(
        {
            "hook_event_name": "afterShellExecution",
            "command": "npx tsc --noEmit",
            "output": "error: none\ncompiled ok\n",
            "exitCode": 0,
        }
    )
    assert main(["--db", db, "hook", "--err", payload]) == 0
    assert capsys.readouterr().out == ""
