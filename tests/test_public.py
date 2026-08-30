from pathlib import Path

from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.home import propose_line, share_claim
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.public import (
    _compatible_release,
    _installed_triple,
    eval_is_proof,
    project_public,
    public_eval,
    refine_eval,
)
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
    assert public_eval("uv run pytest -q tests/test_widget_flow.py") == ""
    assert public_eval("npx tsc --noEmit") == "npx tsc --noEmit"
    assert public_eval("true") == "true"
    assert public_eval("python3 check.py") == "python3 check.py"
    assert public_eval("node check.mjs") == "node check.mjs"
    assert public_eval("node evals/mcp-protocol-version.mjs") == "node evals/mcp-protocol-version.mjs"
    assert public_eval("python3 /home/runner/check.py") == ""
    # stripped tree recipe is empty, not rewritten as the tautology hint
    assert public_eval("uv run pytest -q tests/test_widget_flow.py") != "true"


def test_refine_eval_exact_pin_checks_version_not_import():
    from claimidx.policy import eval_allowed

    py = refine_eval("true", fix_k="pin", fix_b="pydantic==2.7.0", eco="py")
    assert "importlib.metadata" in py
    assert "2.7.0" in py
    assert "import pydantic" not in py
    ok, why = eval_allowed(py)
    assert ok, why
    rng = refine_eval("true", fix_k="pin", fix_b="pydantic>=2.7", eco="py")
    assert "importlib.metadata" in rng
    assert "import pydantic" not in rng
    assert "(2, 7, 0)" in rng
    ok, why = eval_allowed(rng)
    assert ok, why
    npm = refine_eval("true", fix_k="pin", fix_b="left-pad@1.3.0", eco="npm")
    assert "1.3.0" in npm and "package.json" in npm
    ok, why = eval_allowed(npm)
    assert ok, why


def test_refine_eval_range_pin_checks_interval_not_import():
    from claimidx.policy import eval_allowed
    from claimidx.sandbox import replay

    rng = refine_eval("true", fix_k="pin", fix_b="pydantic>=2.7,<3", eco="py")
    assert "importlib.metadata" in rng
    assert "import pydantic" not in rng
    assert "(2, 7, 0)" in rng and "(3, 0, 0)" in rng
    assert eval_is_proof(rng) is True
    ok, why = eval_allowed(rng)
    assert ok, why
    held = replay(rng)
    assert held.ran and held.held, held.as_dict()
    miss = refine_eval("true", fix_k="pin", fix_b="pydantic>=99", eco="py")
    missed = replay(miss)
    assert missed.ran and not missed.held, missed.as_dict()
    given = refine_eval('python -c "import pydantic"', fix_k="pin", fix_b="pydantic>=2.7,<3", eco="py")
    assert given == 'python -c "import pydantic"'
    marker = refine_eval("true", fix_k="pin", fix_b="pydantic>=2.7; python_version>='3.11'", eco="py")
    assert marker == "true"
    assert eval_is_proof(marker) is False
    one = refine_eval("true", fix_k="pin", fix_b="pydantic~=1", eco="py")
    assert one == "true"
    assert eval_is_proof(one) is False


def test_refine_eval_compatible_release_desugars_to_interval():
    from claimidx.policy import eval_allowed
    from claimidx.sandbox import replay

    assert _compatible_release("1.4") == [(">=", (1, 4, 0)), ("<", (2, 0, 0))]
    assert _compatible_release("1.4.5") == [(">=", (1, 4, 5)), ("<", (1, 5, 0))]
    assert _compatible_release("1") is None
    tilde = refine_eval("true", fix_k="pin", fix_b="pydantic~=2.7", eco="py")
    assert "importlib.metadata" in tilde
    assert "import pydantic" not in tilde
    assert "(2, 7, 0)" in tilde and "(3, 0, 0)" in tilde
    assert eval_is_proof(tilde) is True
    ok, why = eval_allowed(tilde)
    assert ok, why
    held = replay(tilde)
    assert held.ran and held.held, held.as_dict()
    patch = refine_eval("true", fix_k="pin", fix_b="pydantic~=2.7.0", eco="py")
    assert "(2, 8, 0)" in patch
    missed = replay(patch)
    assert missed.ran and not missed.held, missed.as_dict()


def test_installed_triple_post_local_hold_pre_misses():
    assert _installed_triple("2.7.0.post1") == (2, 7, 0)
    assert _installed_triple("2.7.0+local") == (2, 7, 0)
    assert _installed_triple("2.7.0.post1+local") == (2, 7, 0)
    assert _installed_triple("2.7.0rc1") is None
    assert _installed_triple("2.7.0a1") is None
    assert _installed_triple("2.7.0.dev1") is None
    rng = refine_eval("true", fix_k="pin", fix_b="pydantic>=2.7,<3", eco="py")
    assert "split('+')" in rng
    assert "post" in rng
    assert "(?:a|b|rc|dev)" in rng


def test_public_fix_body_keeps_pins_flags_and_relative_paths():
    pin = project_public(_claim(fix_k="pin", fix_b="pydantic>=2.7,<3"))
    assert pin.fix.b == "pydantic>=2.7,<3"
    heap = project_public(_claim(fix_b="NODE_OPTIONS=--max-old-space-size=4096"))
    assert "4096" in heap.fix.b
    rel = project_public(_claim(fix_b='compilerOptions.paths["@/*"] = ["./src/*"]'))
    assert "./src/*" in rel.fix.b
    py = project_public(_claim(fix_b="pip install setuptools  # distutils removed in Python 3.12"))
    assert "3.12" in py.fix.b


def test_public_fix_body_keeps_uri_schemes():
    pg = project_public(_claim(fix_b="Use postgresql:// not postgres://"))
    assert "postgresql://" in pg.fix.b
    assert "postgres://" in pg.fix.b
    assert "<PATH>" not in pg.fix.b
    docs = project_public(_claim(fix_b="see https://docs.sqlalchemy.org/en/20/"))
    assert "https://" in docs.fix.b
    assert "<PATH>" not in docs.fix.b


def test_public_fix_body_redacts_mailbox_and_home_paths():
    p = project_public(_claim(fix_b=r"mail ops@example.com then edit C:\Users\alice\.env"))
    assert "ops@example.com" not in p.fix.b
    assert "alice" not in p.fix.b
    assert "<STR>" in p.fix.b
    assert "<PATH>" in p.fix.b
    host = project_public(_claim(fix_b="point DATABASE_URL at localhost"))
    assert "localhost" not in host.fix.b
    assert "<HOST>" in host.fix.b


def test_projection_drops_note_and_local_eval_keeps_fingerprint():
    c = _claim(
        note="internal ticket 99 do not ship",
        eval="uv run pytest -q tests/test_widget_flow.py",
        tried=["C:\\Users\\alice\\app\\retry"],
    )
    p = project_public(c)
    assert p.id == c.id and p.fp == c.fp
    assert p.note == ""
    assert p.eval.cmd == ""
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
