from pathlib import Path

from claimidx.cli import main


def test_init_requires_an_agent(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--offline"])
    assert rc == 2
    assert "any-name" in capsys.readouterr().err


def test_init_offline_seeds_and_writes_config(tmp_path: Path, capsys, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(cfg))
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "init", "--agent", "harper", "--offline"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "did:claimidx:harper" in out
    assert cfg.exists()
    assert main(["--db", db, "ls"]) == 0
    listed = capsys.readouterr().out
    assert "spr_a11c000000000001" in listed
    hook = tmp_path / "claude" / "settings.json"
    assert hook.exists()
    import json

    data = json.loads(hook.read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "PostToolUseFailure" in blob
    assert "claimidx hook" in blob
    assert main(["--db", db, "doctor"]) in (0, 2)


def test_init_wires_cursor_and_grok_mcp(tmp_path: Path, capsys, monkeypatch):
    import json

    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    cursor = tmp_path / "cursor" / "mcp.json"
    grok = tmp_path / "grok" / "config.toml"
    monkeypatch.setenv("CLAIMIDX_CURSOR_MCP", str(cursor))
    monkeypatch.setenv("CLAIMIDX_GROK_CONFIG", str(grok))
    grok.parent.mkdir(parents=True)
    grok.write_text('[cli]\ntheme = "dark"\n', encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "init", "--agent", "wiretest", "--offline"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["harness"]["cursor"]["status"] == "installed"
    assert out["harness"]["grok"]["status"] == "installed"
    cur = json.loads(cursor.read_text(encoding="utf-8"))
    assert cur["mcpServers"]["claimidx"]["command"] == "claimidx-mcp"
    assert cur["mcpServers"]["claimidx"]["env"]["CLAIMIDX_OWNER"] == "did:claimidx:wiretest"
    assert "HOME_API" not in json.dumps(cur)
    text = grok.read_text(encoding="utf-8")
    assert "[cli]" in text and "theme" in text
    assert "[mcp_servers.claimidx]" in text
    assert "did:claimidx:wiretest" in text
    assert "HOME_API" not in text
    rc2 = main(["--db", db, "init", "--agent", "wiretest", "--offline"])
    assert rc2 == 0
    again = json.loads(capsys.readouterr().out)
    assert again["harness"]["cursor"]["status"] == "present"
    assert again["harness"]["grok"]["status"] == "present"


def test_init_wires_opencode_and_vscode_mcp(tmp_path: Path, capsys, monkeypatch):
    import json

    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    oc = tmp_path / "opencode" / "opencode.json"
    vs = tmp_path / "vscode" / "mcp.json"
    monkeypatch.setenv("CLAIMIDX_OPENCODE_CONFIG", str(oc))
    monkeypatch.setenv("CLAIMIDX_VSCODE_MCP", str(vs))
    oc.parent.mkdir(parents=True)
    oc.write_text('{"$schema": "https://opencode.ai/config.json", "mcp": {}}\n', encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "init", "--agent", "wiretest", "--offline"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["harness"]["opencode"]["status"] == "installed"
    assert out["harness"]["vscode"]["status"] == "installed"
    od = json.loads(oc.read_text(encoding="utf-8"))
    assert od["mcp"]["claimidx"]["command"] == ["claimidx-mcp"]
    assert od["mcp"]["claimidx"]["environment"]["CLAIMIDX_OWNER"] == "did:claimidx:wiretest"
    vd = json.loads(vs.read_text(encoding="utf-8"))
    assert vd["servers"]["claimidx"]["command"] == "claimidx-mcp"
    assert "HOME_API" not in json.dumps(od) + json.dumps(vd)


def test_init_updates_mcp_owner_on_reinit(tmp_path: Path, capsys, monkeypatch):
    """claimidx init --agent bob must not leave CLAIMIDX_OWNER as alice."""
    import json

    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    cursor = tmp_path / "cursor" / "mcp.json"
    grok = tmp_path / "grok" / "config.toml"
    oc = tmp_path / "opencode" / "opencode.json"
    vs = tmp_path / "vscode" / "mcp.json"
    monkeypatch.setenv("CLAIMIDX_CURSOR_MCP", str(cursor))
    monkeypatch.setenv("CLAIMIDX_GROK_CONFIG", str(grok))
    monkeypatch.setenv("CLAIMIDX_OPENCODE_CONFIG", str(oc))
    monkeypatch.setenv("CLAIMIDX_VSCODE_MCP", str(vs))
    grok.parent.mkdir(parents=True)
    grok.write_text('[cli]\ntheme = "dark"\n', encoding="utf-8")
    oc.parent.mkdir(parents=True)
    oc.write_text('{"mcp": {}}\n', encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "alice", "--offline"]) == 0
    capsys.readouterr()
    rc = main(["--db", db, "init", "--agent", "bob", "--offline"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["harness"]["cursor"]["status"] == "updated"
    assert out["harness"]["grok"]["status"] == "updated"
    assert out["harness"]["opencode"]["status"] == "updated"
    assert out["harness"]["vscode"]["status"] == "updated"
    cur = json.loads(cursor.read_text(encoding="utf-8"))
    assert cur["mcpServers"]["claimidx"]["env"]["CLAIMIDX_OWNER"] == "did:claimidx:bob"
    text = grok.read_text(encoding="utf-8")
    assert "[cli]" in text and "theme" in text
    assert "did:claimidx:bob" in text
    assert "did:claimidx:alice" not in text
    od = json.loads(oc.read_text(encoding="utf-8"))
    assert od["mcp"]["claimidx"]["environment"]["CLAIMIDX_OWNER"] == "did:claimidx:bob"
    vd = json.loads(vs.read_text(encoding="utf-8"))
    assert vd["servers"]["claimidx"]["env"]["CLAIMIDX_OWNER"] == "did:claimidx:bob"


def test_init_skips_cursor_when_dir_absent(tmp_path: Path, capsys, monkeypatch):
    import json

    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("CLAIMIDX_CURSOR_MCP", raising=False)
    monkeypatch.delenv("CLAIMIDX_GROK_CONFIG", raising=False)
    monkeypatch.setattr("claimidx.hook.cursor_mcp_path", lambda: tmp_path / "no-cursor" / "mcp.json")
    monkeypatch.setattr("claimidx.hook.grok_config_path", lambda: tmp_path / "no-grok" / "config.toml")
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--agent", "skipwire", "--offline"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["harness"]["cursor"]["status"] == "skip"
    assert out["harness"]["grok"]["status"] == "skip"
    assert out["harness"]["opencode"]["status"] == "skip"
    assert out["harness"]["vscode"]["status"] == "skip"
