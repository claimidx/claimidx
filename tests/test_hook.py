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
    raw = json.dumps({
        "hook_event_name": "PostToolUseFailure",
        "tool_result": "ImportError: No module named 'cgi'\n",
    })
    err, event = extract_hook_err(raw)
    assert event == "PostToolUseFailure"
    assert err and "cgi" in err


def test_extract_claude_failure_json():
    raw = json.dumps({
        "hook_event_name": "PostToolUseFailure",
        "tool_name": "Bash",
        "tool_response": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'cgi'\n",
    })
    err, event = extract_hook_err(raw)
    assert event == "PostToolUseFailure"
    assert err and "cgi" in err


def test_extract_skips_successful_posttooluse():
    raw = json.dumps({
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_response": "ok\n",
    })
    err, event = extract_hook_err(raw)
    assert err is None
    assert event == "PostToolUse"


def test_extract_refuses_secret_shaped_stderr():
    raw = json.dumps({
        "hook_event_name": "PostToolUseFailure",
        "tool_response": "error: Bearer supersecrettokenvalue123456\n",
    })
    err, event = extract_hook_err(raw)
    assert err is None
    assert event == "PostToolUseFailure"


def test_hook_cli_hit_and_claude_context(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    payload = json.dumps({
        "hook_event_name": "PostToolUseFailure",
        "tool_response": "TypeError: params is a Promise\n",
    })
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
    assert main([
        "--db", db, "--fmt", "id", "publish",
        "--err", err, "--eco", "npm", "--rt", "node@18", "--dep", "next@15.0.0",
        "--fix-k", "patch", "--fix-b", "await params", "--eval", "true",
    ]) == 0
    a = capsys.readouterr().out.strip()
    assert main([
        "--db", db, "--fmt", "id", "publish",
        "--err", err, "--eco", "npm", "--rt", "node@20", "--dep", "next@15.0.0",
        "--fix-k", "patch", "--fix-b", "await searchParams", "--eval", "true",
    ]) == 0
    b = capsys.readouterr().out.strip()
    assert a != b
    payload = json.dumps({
        "hook_event_name": "PostToolUseFailure",
        "tool_response": err + "\n",
    })
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
    from claimidx.hook import claude_hook_block, hook_command

    if os.name != "nt":
        return
    assert hook_command().lstrip().startswith("&")
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
    assert rec2["status"] == "present"
    assert "did:claimidx:other" not in path.read_text(encoding="utf-8")


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
    rc = main(["--db", db, "init", "--agent", "harper", "--offline", "--no-hooks"])
    assert rc == 0
    assert not (tmp_path / "claude" / "settings.json").exists()


def test_hook_cli_miss_is_silent(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setattr("sys.stdin", StringIO("definitely-not-a-known-error-xyzzy\n"))
    rc = main(["--db", db, "hook", "--eco", "py"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""


def test_mcp_hook_is_evidence_only(tmp_path):
    store = Store(tmp_path / "ix.sqlite")
    err = "TypeError: params is a Promise"
    store.put(Claim(
        fp=fingerprint(err=err, cls="type_error", eco="npm", dep=["next@15.0.0"]),
        cls="type_error",
        err=normalize_error(err),
        eco="npm",
        dep=["next@15.0.0"],
        fix=Fix(k="patch", b="await params"),
        eval=EvalSpec(cmd="true"),
        own="did:claimidx:test",
    ))
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "claimidx_hook",
                "arguments": {
                    "raw": json.dumps({
                        "hook_event_name": "PostToolUseFailure",
                        "tool_response": err + "\n",
                    }),
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
