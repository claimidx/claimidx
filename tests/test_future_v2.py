from pathlib import Path
import sqlite3

import pytest

from claimidx.extractors import extract_plugin_features, plugin_inventory
from claimidx.graph import ProtocolEvent
from claimidx.mcp_server import TOOLS, _call
from claimidx.public import projection_preview
from claimidx.query import ingest
from claimidx.store import Store


def test_projection_preview_preserves_fingerprint_and_explains_redaction(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    result = ingest(
        "TypeError: private projection",
        fix_k="constraint",
        fix_b="Inspect C:/Users/private/project/file.py and notify person@example.com",
        eval="python C:/Users/private/project/check.py",
        tried=["C:/Users/private/project/attempt.py"],
        note="private local note",
        db=db,
    )
    claim = Store(db).get(result["id"])
    assert claim is not None
    preview = projection_preview(claim)
    assert preview["safe"] is True
    assert preview["fingerprint_preserved"] is True
    assert "note" in preview["removed"]
    assert "fix.b" in preview["transformed"]
    assert "<PATH>" in preview["projection"]["fix"]["b"]
    assert "<STR>" in preview["projection"]["fix"]["b"]
    assert preview["projection"]["eval"]["cmd"] == ""


def test_feature_plugins_are_additive_and_fail_open(monkeypatch):
    class Plugin:
        name = "sample"
        version = "1"

        def extract(self, raw_error, context):
            return {"ticket": context.get("ticket"), "length": len(raw_error)}

    monkeypatch.setattr("claimidx.extractors.installed_extractors", lambda: [Plugin()])
    assert plugin_inventory() == [{"name": "sample", "version": "1", "scope": "additive-features-only"}]
    assert extract_plugin_features("TypeError", {"ticket": "T-1"}) == {"sample@1": {"ticket": "T-1", "length": 9}}


def test_broken_feature_plugin_cannot_break_retrieval(monkeypatch):
    class Broken:
        name = "broken"
        version = "1"

        def extract(self, raw_error, context):
            raise RuntimeError("plugin failed")

    monkeypatch.setattr("claimidx.extractors.installed_extractors", lambda: [Broken()])
    assert extract_plugin_features("TypeError") == {"broken@1": {"error": "RuntimeError"}}


def test_protocol_event_sync_is_cursor_based_and_idempotent(tmp_path: Path):
    source = Store(tmp_path / "source.sqlite")
    source.log("ask", "did:claimidx:test", detail={"hit": False, "n": 0})
    first = source.protocol_events()
    assert first["n"] == 1
    assert len(first["batch_hash"]) == 64
    assert source.protocol_events(after=first["next_cursor"])["n"] == 0

    target = Store(tmp_path / "target.sqlite")
    assert target.import_protocol_events(first["events"]) == {"accepted": 1, "duplicate": 0}
    assert target.import_protocol_events(first["events"]) == {"accepted": 0, "duplicate": 1}


def test_protocol_event_rejects_non_did_actor():
    with pytest.raises(ValueError, match="did:"):
        ProtocolEvent(kind="confirm", actor="not-a-did")


def test_existing_v2_database_backfills_fts_and_legacy_events(tmp_path: Path):
    db = tmp_path / "upgrade.sqlite"
    result = ingest(
        "ModuleNotFoundError: No module named 'upgrade_pkg'",
        fix_k="pin",
        fix_b="upgrade-pkg>=1",
        eval='python -c "import upgrade_pkg"',
        eco="py",
        db=db,
    )
    with sqlite3.connect(db) as con:
        con.execute("DELETE FROM claims_fts")
        con.execute("DELETE FROM protocol_events_v2")
        con.execute("DELETE FROM metadata WHERE key IN ('fts_backfill','event_backfill')")
    upgraded = Store(db)
    assert upgraded.candidates(fp=result["fp"], err="upgrade_pkg", cls="missing_module")
    assert upgraded.protocol_events()["n"] >= 1


def test_mcp_exposes_v2_preview_explain_proof_and_alternatives(tmp_path: Path):
    names = {tool["name"] for tool in TOOLS}
    assert {"claimidx_explain", "claimidx_share_preview", "claimidx_proof_validate", "claimidx_proof_run"} <= names
    publish = next(tool for tool in TOOLS if tool["name"] == "claimidx_publish")
    assert "alternative" in publish["inputSchema"]["properties"]

    store = Store(tmp_path / "mcp.sqlite")
    base = {
        "err": "ModuleNotFoundError: No module named 'mcp_v2_pkg'",
        "fix_k": "pin",
        "fix_b": "mcp-v2-pkg>=1",
        "eval": 'python -c "import mcp_v2_pkg"',
        "eco": "py",
        "own": "did:claimidx:agent-a",
    }
    first = _call("claimidx_publish", base, store)
    second = _call("claimidx_publish", {**base, "fix_b": "Use the compatibility module", "alternative": True}, store)
    assert first["id"] != second["id"]
    graph = _call("claimidx_explain", {"id": second["id"]}, store)
    assert graph["relations"][0]["kind"] == "alternative"
    preview = _call("claimidx_share_preview", {"id": second["id"]}, store)
    assert preview["fingerprint_preserved"] is True
