"""Unreleased: historic L445 pull exclude, and CLI doctor version trio."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claimidx import __version__
from claimidx.fingerprint import fingerprint, fingerprint_accepted
from claimidx.home import (
    PULL_EXCLUDED_IDS,
    parse_ledger,
    parse_ledger_report,
    pull,
    pull_skip_lines,
)
from claimidx.store import Store


LEDGER = Path(__file__).resolve().parents[1] / "data" / "claims.jsonl"

# Live commons row (0.7.22 first-run audit share). Stored err still has a
# half-normalized venv path; today's normalize_error collapses it to <PATH>.
HISTORIC_0722_AUDIT = {
    "v": 1,
    "id": "cix_23fb9d61fa06544d",
    "fp": "8e78eda6a6771b15fc2ce5fef70316199c1df365da0ed845edc236dcedc51b44",
    "cls": "module_not_found",
    "err": "/workspace/firstrun-audit-<N>.<N>.<N>-<HEX>/venv-py313/bin/python: No module named pytest",
    "eco": "other",
    "rt": "py@3.13",
    "dep": [],
    "tool": [],
    "tried": [],
    "fix": {"k": "constraint", "b": "pytest"},
    "eval": {"cmd": 'python -c "import pytest"', "expect": 0},
    "st": "proposed",
    "nc": 0,
    "nf": 0,
    "own": "did:claimidx:falsifier-0722-audit",
    "model": "",
    "ts": "2026-11-12T13:49:56.348Z",
    "note": "",
    "src": "home",
}


def test_ledger_regression_packaged_claims_keep_fingerprints():
    raw_lines = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line.strip()]
    stored = {row["id"]: row["fp"] for row in raw_lines}
    claims, skipped = parse_ledger(LEDGER.read_text(encoding="utf-8"))
    assert skipped == []
    assert len(claims) == len(stored)
    for c in claims:
        assert c.fp == stored[c.id]
        assert fingerprint_accepted(c.fp, err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=list(c.dep or []))


def test_historic_0722_audit_row_is_fp_mismatch_not_fieldset():
    """Root cause: stored fingerprint does not recompute (not malformed, not field-set)."""
    from claimidx.fieldset import fieldset_rejection

    assert HISTORIC_0722_AUDIT["id"] in PULL_EXCLUDED_IDS
    assert fieldset_rejection(dict(HISTORIC_0722_AUDIT)) is None
    assert (
        fingerprint_accepted(
            HISTORIC_0722_AUDIT["fp"],
            err=HISTORIC_0722_AUDIT["err"],
            cls=HISTORIC_0722_AUDIT["cls"],
            eco=HISTORIC_0722_AUDIT["eco"],
            rt=HISTORIC_0722_AUDIT["rt"],
            dep=list(HISTORIC_0722_AUDIT["dep"]),
        )
        is False
    )
    # Today's hash of the same fields is different; we do not rewrite the stored fp.
    current = fingerprint(
        err=HISTORIC_0722_AUDIT["err"],
        cls=HISTORIC_0722_AUDIT["cls"],
        eco=HISTORIC_0722_AUDIT["eco"],
        rt=HISTORIC_0722_AUDIT["rt"],
        dep=list(HISTORIC_0722_AUDIT["dep"]),
    )
    assert current != HISTORIC_0722_AUDIT["fp"]


def _good_row() -> dict:
    err = "ModuleNotFoundError: No module named claimidx_0729f_ok"
    cls = "module_not_found"
    eco, rt, dep = "py", f"py@{sys.version_info.major}.{sys.version_info.minor}", []
    return {
        "v": 1,
        "id": "cix_0729f00000000001",
        "fp": fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep),
        "cls": cls,
        "err": err,
        "eco": eco,
        "rt": rt,
        "dep": dep,
        "tool": [],
        "tried": [],
        "fix": {"k": "constraint", "b": "claimidx_0729f_ok"},
        "eval": {"cmd": 'python -c "import json"', "expect": 0},
        "st": "proposed",
        "nc": 0,
        "nf": 0,
        "own": "did:claimidx:agent-0729f",
        "src": "home",
    }


def test_historic_0722_audit_row_is_excluded_not_skipped(tmp_path: Path, monkeypatch):
    good = _good_row()
    # A different fp-mismatch must still be a skip, not silently excluded.
    other_mismatch = dict(good)
    other_mismatch["id"] = "cix_0729f00000000002"
    other_mismatch["fp"] = "0" * 64
    other_mismatch["err"] = "ModuleNotFoundError: No module named claimidx_0729f_mismatch"
    text = "\n".join(json.dumps(r) for r in (HISTORIC_0722_AUDIT, good, other_mismatch)) + "\n"
    accepted, skipped, excluded = parse_ledger_report(text)
    assert [c.id for c in accepted] == [good["id"]]
    assert skipped and "fp mismatch" in skipped[0] and "cix_23fb9d61fa06544d" not in "".join(skipped)
    assert len(excluded) == 1 and "cix_23fb9d61fa06544d" in excluded[0]
    assert "excluded" in excluded[0]

    def fake_fetch(url=None):
        return parse_ledger_report(text) + ("https://example.invalid/claims.jsonl",)

    monkeypatch.setattr("claimidx.home.fetch_ledger_report", fake_fetch)
    result = pull(Store(tmp_path / "ix.sqlite"))
    assert result["skipped_n"] == 1
    assert result["excluded_n"] == 1
    assert "cix_23fb9d61fa06544d" in result["excluded"][0]
    assert result["imported"] == 1
    assert Store(tmp_path / "ix.sqlite").get("cix_23fb9d61fa06544d") is None
    lines = "\n".join(pull_skip_lines(result))
    assert "excluded" in lines and "cix_23fb9d61fa06544d" in lines
    assert "skipped" in lines and "fp mismatch" in lines


def test_cli_doctor_lifts_version_trio(tmp_path: Path, capsys, monkeypatch):
    from claimidx.cli import main

    monkeypatch.setenv("CLAIMIDX_COMMONS", "0")
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "doctor"])
    assert rc in (0, 2)
    captured = capsys.readouterr()
    assert f"package_version={__version__}" in captured.err
    assert f"server_info_version={__version__}" in captured.err
    assert "version_mismatch=false" in captured.err
    body = json.loads(captured.out)
    assert body["package_version"] == __version__
    assert body["server_info_version"] == __version__
    assert body["version_mismatch"] is False

    rc = main(["--db", db, "--fmt", "json", "doctor"])
    assert rc in (0, 2)
    captured = capsys.readouterr()
    body = json.loads(captured.out)
    assert body["package_version"] == __version__
    assert body["server_info_version"] == __version__
    assert body["version_mismatch"] is False
