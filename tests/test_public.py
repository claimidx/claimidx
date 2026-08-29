from pathlib import Path

from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.home import propose_line, share_claim
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.public import project_public, public_eval
from claimidx.store import Store


def _claim(**kw) -> Claim:
    err = kw.pop("err", "TypeError: foo is not a function")
    return Claim(
        fp=fingerprint(err=err, eco="npm", rt="node@20", dep=["lib@1.0"]),
        cls="type_error",
        err=normalize_error(err),
        eco="npm",
        rt="node@20",
        dep=["lib@1.0"],
        tried=kw.pop("tried", ["retry"]),
        fix=Fix(k=kw.pop("fix_k", "patch"), b=kw.pop("fix_b", "await foo()")),
        eval=EvalSpec(cmd=kw.pop("eval", "npx tsc --noEmit")),
        own="did:claimidx:codex",
        note=kw.pop("note", ""),
        src="local",
        **kw,
    )


def test_public_eval_strips_project_paths():
    assert public_eval("uv run pytest -q tests/test_widget_flow.py") == "true"
    assert public_eval("npx tsc --noEmit") == "npx tsc --noEmit"
    assert public_eval("true") == "true"


def test_projection_drops_note_and_local_eval_keeps_fingerprint():
    c = _claim(
        note="internal ticket 99 do not ship",
        eval="uv run pytest -q tests/test_widget_flow.py",
        tried=["C:\\Users\\alice\\app\\retry"],
    )
    p = project_public(c)
    assert p.id == c.id and p.fp == c.fp
    assert p.note == ""
    assert p.eval.cmd == "true"
    assert p.tried == []
    assert "ticket" not in p.model_dump_json()


def test_outbox_line_has_no_home_paths(tmp_path: Path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    monkeypatch.setenv("CLAIMIDX_OUTBOX", str(outbox))
    monkeypatch.delenv("CLAIMIDX_HOME_API", raising=False)
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim(
        note="secret project nickname",
        eval="python -m pytest tests/test_internal.py",
        fix_b="const x = await params",
    ))
    result = share_claim(store, c)
    assert result["status"] == "outbox"
    line = outbox.read_text(encoding="utf-8")
    assert "secret project" not in line
    assert "test_internal" not in line
    assert "const x = await params" in line
    assert propose_line(c)
