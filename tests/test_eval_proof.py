"""Hint evals are hits, not proof. The public ledger should not fill with them silently."""

import json
from pathlib import Path

from claimidx import ingest
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.home import share_claim
from claimidx.mcp_server import handle
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.public import eval_is_proof
from claimidx.store import Store

OWN = "did:claimidx:test"


def _claim(eval_cmd: str, err: str = "ModuleNotFoundError: No module named 'proof_demo'") -> Claim:
    return Claim(
        fp=fingerprint(err=err, eco="py", rt="py@3.13", dep=["proof-demo@1.0"]),
        cls="module_not_found",
        err=normalize_error(err),
        eco="py",
        rt="py@3.13",
        dep=["proof-demo@1.0"],
        fix=Fix(k="constraint", b="proof-demo needs py>=3.11"),
        eval=EvalSpec(cmd=eval_cmd),
        own=OWN,
        src="local",
    )


def test_version_banners_are_hints_not_proof():
    for cmd in ("true", "false", "go version", "node --version", "pip --version", "pytest --version", "java -version", "cargo -V", ""):
        assert not eval_is_proof(cmd), cmd
    assert eval_is_proof('python -c "import proof_demo"')
    assert eval_is_proof("go build ./...")


def test_python_ingest_reports_eval_proof(tmp_path: Path):
    db = tmp_path / "ix.sqlite"
    out = ingest("ModuleNotFoundError: No module named 'pi_demo'", fix_k="constraint", fix_b="needs py>=3.11", eval="true", eco="py", own=OWN, db=db)
    assert out["eval_proof"] is False
    assert "hint" in out["warn"]
    out2 = ingest(
        "ModuleNotFoundError: No module named 'pi_demo2'",
        fix_k="constraint",
        fix_b="needs py>=3.11",
        eval='python -c "import pi_demo2"',
        eco="py",
        own=OWN,
        db=db,
    )
    assert out2["eval_proof"] is True
    assert "warn" not in out2


def test_mcp_ingest_reports_eval_proof(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "claimidx_ingest",
                "arguments": {
                    "err": "ModuleNotFoundError: No module named 'mcp_demo'",
                    "eco": "py",
                    "fix_k": "constraint",
                    "fix_b": "needs py>=3.11",
                    "eval": "go version",
                    "own": OWN,
                },
            },
        },
        store,
    )
    body = json.loads(rec["result"]["content"][0]["text"])
    assert body["eval_proof"] is False
    assert "hint" in body["warn"]


def test_outbox_share_skips_hint_evals_unless_forced(tmp_path: Path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(outbox))
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    store = Store(tmp_path / "agent.sqlite")
    hint = store.put(_claim("true"))
    result = share_claim(store, hint)
    assert result["status"] == "skipped"
    assert "hint" in result["reason"]
    assert not outbox.exists()
    forced = share_claim(store, hint, force=True)
    assert forced["status"] == "outbox"
    proof = store.put(_claim('python -c "import proof_demo"', err="ModuleNotFoundError: No module named 'proof_demo2'"))
    assert share_claim(store, proof)["status"] == "outbox"
