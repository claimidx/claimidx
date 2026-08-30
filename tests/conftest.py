from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_claimidx(tmp_path, monkeypatch):
    """Keep tests off the operator's ~/.claimidx config and live home API."""
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(tmp_path / "outbox.jsonl"))
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:test")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    monkeypatch.delenv("CLAIMIDX_HOME_TOKEN", raising=False)
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    monkeypatch.delenv("CLAIMIDX_CURSOR_MCP", raising=False)
    monkeypatch.delenv("CLAIMIDX_GROK_CONFIG", raising=False)
    monkeypatch.delenv("CLAIMIDX_OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("CLAIMIDX_VSCODE_MCP", raising=False)

    def _cursor():
        override = os.environ.get("CLAIMIDX_CURSOR_MCP")
        return Path(override) if override else tmp_path / "absent-cursor" / "mcp.json"

    def _grok():
        override = os.environ.get("CLAIMIDX_GROK_CONFIG")
        return Path(override) if override else tmp_path / "absent-grok" / "config.toml"

    def _opencode():
        override = os.environ.get("CLAIMIDX_OPENCODE_CONFIG")
        return Path(override) if override else tmp_path / "absent-opencode" / "opencode.json"

    def _vscode():
        override = os.environ.get("CLAIMIDX_VSCODE_MCP")
        return Path(override) if override else tmp_path / "absent-vscode" / "mcp.json"

    monkeypatch.setattr("claimidx.hook.cursor_mcp_path", _cursor)
    monkeypatch.setattr("claimidx.hook.grok_config_path", _grok)
    monkeypatch.setattr("claimidx.hook.opencode_config_path", _opencode)
    monkeypatch.setattr("claimidx.hook.vscode_mcp_path", _vscode)
