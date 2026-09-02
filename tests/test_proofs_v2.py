import sys
from pathlib import Path

import pytest

from claimidx.graph import Bundle, Failure, Proof, Remedy, canonical_hash
from claimidx.identity import generate_identity, sign_record, verify_record
from claimidx.proofs import proof_template, run_proof, validate_proof
from claimidx.query import ingest
from claimidx.store import Store


def test_structured_proof_runs_without_a_shell():
    proof = proof_template(sys.executable, ["-c", "raise SystemExit(0)"], expect_exit=0)
    result = run_proof(proof)
    assert result["held"] is True
    assert result["sandbox"] == "argv-allowlist"


def test_structured_proof_rejects_non_allowlisted_program():
    proof = Proof.model_validate(
        {
            "steps": [
                {"op": "run", "program": "powershell", "args": ["-Command", "whoami"]},
                {"op": "expect_exit", "code": 0},
            ]
        }
    )
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_proof(proof)


def test_ed25519_did_key_signatures_detect_tampering(tmp_path: Path):
    key = tmp_path / "identity.json"
    identity = generate_identity(key)
    record = {"v": 2, "id": "rmd_0123456789abcdef", "key_id": "", "signature": "", "value": 7}
    signed = sign_record(record, key)
    assert signed["key_id"] == identity["did"]
    assert verify_record(signed) is True
    signed["value"] = 8
    assert verify_record(signed) is False


def test_signed_bundle_is_accepted_and_invalid_signature_is_refused(tmp_path: Path):
    source = tmp_path / "source.sqlite"
    written = ingest(
        "TypeError: signed bundle",
        fix_k="constraint",
        fix_b="Use the verified form.",
        eval="python -c \"raise SystemExit(0)\"",
        db=source,
    )
    graph = Store(source).graph(written["id"])
    assert graph is not None
    key = tmp_path / "identity.json"
    identity = generate_identity(key)
    remedy = dict(graph["remedy"])
    remedy["own"] = identity["did"]
    remedy["key_id"] = identity["did"]
    remedy["signature"] = ""
    remedy["content_hash"] = ""
    remedy["content_hash"] = canonical_hash(remedy)
    remedy = sign_record(remedy, key)
    bundle = Bundle(
        failure=Failure.model_validate(graph["failure"]),
        proof=Proof.model_validate(graph["proof"]),
        remedy=Remedy.model_validate(remedy),
    )
    target = Store(tmp_path / "target.sqlite")
    target.publish_bundle(bundle)
    assert target.failure_graph(bundle.failure.fp_v1) is not None
    broken = bundle.model_copy(deep=True)
    broken.remedy.signature = broken.remedy.signature[:-2] + "aa"
    with pytest.raises(ValueError, match="invalid remedy signature"):
        target.publish_bundle(broken)


def test_v1_ingest_can_attach_structured_proof(tmp_path: Path):
    db = tmp_path / "index.sqlite"
    proof = proof_template("python", ["-c", "raise SystemExit(0)"], expect_exit=0)
    proof_path = tmp_path / "proof.json"
    proof_path.write_text(proof.model_dump_json(), encoding="utf-8")
    from claimidx.cli import main

    assert (
        main(
            [
                "--db",
                str(db),
                "--fmt",
                "id",
                "ingest",
                "--err",
                "TypeError: structured attachment",
                "--fix-k",
                "constraint",
                "--fix-b",
                "Use the structured API.",
                "--eval",
                "python -c \"raise SystemExit(0)\"",
                "--proof",
                str(proof_path),
            ]
        )
        == 0
    )
    claim_id = Store(db).all()[0].id
    graph = Store(db).graph(claim_id)
    assert graph is not None
    assert graph["proof"]["id"] == proof.id
