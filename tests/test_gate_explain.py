"""A miss can name the token, the family dep, or a digest mismatch. Rank stays put."""

from __future__ import annotations

from types import SimpleNamespace

from claimidx.fingerprint import classify, fingerprint, normalize_error
from claimidx.graph import DepDigest
from claimidx.match import rank
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.query import format_gate_line, gate_explain, miss_enrichment


def _claim(err: str, *, eco: str = "py", cls: str | None = None, dep: list[str] | None = None) -> Claim:
    cls = cls or classify(err)
    dep = dep or []
    return Claim(
        fp=fingerprint(err=err, cls=cls, eco=eco, rt="py@3.12", dep=dep),
        cls=cls,
        err=normalize_error(err),
        eco=eco,
        rt="py@3.12",
        dep=dep,
        fix=Fix(k="constraint", b="x"),
        eval=EvalSpec(cmd="true"),
        own="did:claimidx:test",
        nc=4,
        st="confirmed",
    )


def test_disjoint_schema_literals_name_the_payload_and_do_not_rank():
    thumbs = _claim(
        "pydantic.ValidationError: 1 validation error for Reaction\n"
        "emoji\n"
        "  Input should be 'thumbs_up' [type=literal_error, input_value='thumbs_down', input_type=str]",
        eco="py",
        dep=["pydantic@2"],
    )
    q = {
        "err": (
            "pydantic.ValidationError: 1 validation error for Reaction\n"
            "user\n"
            "  Input should be a valid string [type=string_type, input_value='@foo', input_type=str]"
        ),
        "eco": "py",
        "cls": "schema_break",
        "dep": ["pydantic@2"],
        "rt": "py@3.12",
    }
    assert rank(q, [thumbs]) == []
    rows = gate_explain(None, q, [thumbs])
    assert rows
    claim_only = rows[0]["claim_only"]
    assert "thumbs_up" in claim_only or "thumbs_down" in claim_only
    assert "query_only" in rows[0]
    assert "family_dep" not in rows[0]
    assert "digest_mismatch" not in rows[0]
    assert "thumbs_up" in format_gate_line(rows[0]) or "thumbs_down" in format_gate_line(rows[0])


def test_family_dep_names_the_pin_that_completes_the_fingerprint():
    err = "TypeError: widget broke"
    claim = _claim(err, dep=["widget@1"])
    q = {"err": err, "cls": claim.cls, "eco": "py", "rt": "py@3.12", "dep": []}
    rows = gate_explain(None, q, [claim])
    assert rows
    assert rows[0]["family_dep"] == ["widget@1"]
    assert "family_dep widget@1" in format_gate_line(rows[0])


def test_digest_mismatch_names_a_pin_whose_local_bytes_differ(tmp_path):
    err = "TypeError: widget bytes drifted"
    claim = _claim(err, dep=["widget@1"])
    (tmp_path / "widget.py").write_text("not-the-recorded-bytes\n", encoding="utf-8")
    proof = SimpleNamespace(observed_digest=[DepDigest(dep="widget@1", hex="aa" * 32)])

    class _Store:
        def proof_for(self, claim_id):
            assert claim_id == claim.id
            return proof

    q = {"err": err, "cls": claim.cls, "eco": "py", "rt": "py@3.12", "dep": ["widget@1"]}
    rows = gate_explain(_Store(), q, [claim], cwd=str(tmp_path))
    assert rows
    assert rows[0]["digest_mismatch"] == ["widget@1"]
    assert "family_dep" not in rows[0]
    assert "digest_mismatch widget@1" in format_gate_line(rows[0])


def test_empty_miss_omits_gate():
    class _Empty:
        def by_fp(self, fp):
            return []

    q = {"err": "no such failure xyzzy", "fp": "deadbeef", "cls": "other", "eco": "py", "rt": "", "dep": []}
    out = miss_enrichment(_Empty(), q, [])
    assert "gate" not in out
    assert out["near"] == []
    assert out["dead_ends"] == []
