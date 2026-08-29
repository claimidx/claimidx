from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_spoor(tmp_path, monkeypatch):
    """Keep tests off the operator's ~/.claimidx config and live home API."""
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(tmp_path / "outbox.jsonl"))
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:test")
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    monkeypatch.delenv("CLAIMIDX_HOME_TOKEN", raising=False)
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    monkeypatch.delenv("SPOOR_OWNER", raising=False)
    monkeypatch.delenv("SPOOR_AGENT", raising=False)
    monkeypatch.delenv("SPOOR_HOME_API", raising=False)
    monkeypatch.delenv("SPOOR_HOME_TOKEN", raising=False)
    monkeypatch.delenv("SPOOR_HOME", raising=False)
    monkeypatch.delenv("SPOOR_SHARE", raising=False)
    monkeypatch.delenv("SPOOR_CONFIG", raising=False)
