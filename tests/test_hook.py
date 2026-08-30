import json
from io import StringIO

from claimidx.cli import main
from claimidx.hook import extract_hook_err


def test_extract_raw_error_line():
    err, event = extract_hook_err("ModuleNotFoundError: No module named 'cgi'\n")
    assert err and "cgi" in err
    assert event is None


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


def test_hook_cli_miss_is_silent(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "ix.sqlite")
    monkeypatch.setattr("sys.stdin", StringIO("definitely-not-a-known-error-xyzzy\n"))
    rc = main(["--db", db, "hook", "--eco", "py"])
    out = capsys.readouterr().out
    assert rc == 0
    assert out == ""
