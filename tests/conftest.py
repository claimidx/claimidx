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
    # The commons is a real public home. Tests that exercise it set CLAIMIDX_COMMONS=1 and stub the transport.
    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    monkeypatch.delenv("CLAIMIDX_COMMONS_API", raising=False)
    monkeypatch.delenv("CLAIMIDX_CURSOR_MCP", raising=False)
    monkeypatch.delenv("CLAIMIDX_GROK_CONFIG", raising=False)
    monkeypatch.delenv("CLAIMIDX_OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("CLAIMIDX_VSCODE_MCP", raising=False)
    monkeypatch.delenv("CLAIMIDX_GEMINI_CONFIG", raising=False)
    monkeypatch.delenv("CLAIMIDX_CODEX_CONFIG", raising=False)
    monkeypatch.delenv("CLAIMIDX_CODEX_HOOKS", raising=False)
    monkeypatch.delenv("CLAIMIDX_CLINE_MCP", raising=False)
    monkeypatch.delenv("CLAIMIDX_CONTINUE_MCP", raising=False)
    monkeypatch.delenv("CLAIMIDX_WINDSURF_MCP", raising=False)

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

    def _gemini():
        override = os.environ.get("CLAIMIDX_GEMINI_CONFIG")
        return Path(override) if override else tmp_path / "absent-gemini" / "settings.json"

    def _codex():
        override = os.environ.get("CLAIMIDX_CODEX_CONFIG")
        return Path(override) if override else tmp_path / "absent-codex" / "config.toml"

    def _codex_hooks():
        override = os.environ.get("CLAIMIDX_CODEX_HOOKS")
        return Path(override) if override else _codex().parent / "hooks.json"

    def _cline():
        override = os.environ.get("CLAIMIDX_CLINE_MCP")
        return Path(override) if override else tmp_path / "absent-cline" / "data" / "settings" / "cline_mcp_settings.json"

    def _continue():
        override = os.environ.get("CLAIMIDX_CONTINUE_MCP")
        return Path(override) if override else tmp_path / "absent-continue" / "mcpServers" / "claimidx.json"

    def _windsurf():
        override = os.environ.get("CLAIMIDX_WINDSURF_MCP")
        return Path(override) if override else tmp_path / "absent-windsurf" / "mcp_config.json"

    monkeypatch.setattr("claimidx.hook.cursor_mcp_path", _cursor)
    monkeypatch.setattr("claimidx.hook.grok_config_path", _grok)
    monkeypatch.setattr("claimidx.hook.opencode_config_path", _opencode)
    monkeypatch.setattr("claimidx.hook.vscode_mcp_path", _vscode)
    monkeypatch.setattr("claimidx.hook.gemini_settings_path", _gemini)
    monkeypatch.setattr("claimidx.hook.codex_config_path", _codex)
    monkeypatch.setattr("claimidx.hook.codex_hooks_path", _codex_hooks)
    monkeypatch.setattr("claimidx.hook.cline_mcp_path", _cline)
    monkeypatch.setattr("claimidx.hook.continue_mcp_path", _continue)
    monkeypatch.setattr("claimidx.hook.windsurf_mcp_path", _windsurf)
