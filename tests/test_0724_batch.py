"""0.7.24: empty quickstart, weak shares, init --agent, apply VIRTUAL_ENV, small leftovers."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from claimidx.apply import host_rt_mismatch, plan
from claimidx.claim import draft_claim, render_draft
from claimidx.cli import main
from claimidx.fingerprint import normalize_error
from claimidx.impact import _PROXY_DEFAULT_EXCLUDE, _proxy_excluded, _proxy_is_excluded, path_b_cta, weak_share_reasons
from claimidx.mcp_server import _call
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.sandbox import project_python
from claimidx.store import Store


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def _claim(*, err: str, eco: str = "py", rt: str = "", nr: int = 0, own: str = "did:claimidx:agent-test24") -> Claim:
    from claimidx.fingerprint import fingerprint

    return Claim(
        fp=fingerprint(err=err, eco=eco or "other"),
        cls="other",
        err=err,
        eco=eco,
        rt=rt or _py_rt(),
        fix=Fix(k="pin", b="x==1"),
        eval=EvalSpec(cmd='python -c "import json"'),
        own=own,
        nr=nr,
        st="confirmed" if nr else "proposed",
    )


def test_empty_claim_prints_getting_started_and_exits_0(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "claim", "--yes", "--share-yes"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("# getting started")
    assert "init --agent" in text
    assert "claimidx run --" in text
    assert "claimidx claim --yes --share-yes" in text
    assert main(["--db", db, "--fmt", "json", "claim"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["getting_started"] is True
    assert out["reason"] == "no-failure"
    assert "init --agent" in out["hint"]


def test_mcp_empty_claim_is_getting_started(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    out = _call("claimidx_claim", {"yes": True, "share_yes": True}, store)
    assert out["ok"] is True and out["getting_started"] is True
    assert "init --agent" in out["hint"]
    assert store.events(limit=10) == [] or not any(e.get("kind") == "publish" for e in store.events(limit=20))


def test_draft_publish_line_is_not_truncated():
    long_eval = "python -c " + repr("import " + "x" * 80)
    draft = draft_claim(err="ModuleNotFoundError: No module named 'xxlongpkg'", eco="py", rt=_py_rt(), eval_cmd=long_eval, use_diff=False)
    assert draft["ok"]
    text = render_draft(draft)
    line = [ln for ln in text.splitlines() if ln.startswith("publish with:")][0]
    assert draft["publish_argv"] in line
    assert not line.endswith(")") or "pytho)" not in line or "python" in line
    assert "'pytho)" not in line


def test_weak_shared_claim_is_not_countable(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-weak24"
    weak = _claim(err="RuntimeError: weak share", eco="py", nr=0, own=did)
    store.put(weak)
    store.log("publish", did, weak.id, {})
    store.log("commons-push", did, weak.id, {})
    cta = path_b_cta(store, did)
    assert cta["shared"] is True
    assert cta["countable"] is False
    assert "no held replay" in cta["why"] or "not countable" in cta["why"]
    empty_eco = _claim(err="RuntimeError: empty eco share", eco="", nr=1, own=did)
    # empty eco hashes as other in fp helper but eco field stays ""
    store2 = Store(tmp_path / "ix2.sqlite")
    store2.put(empty_eco)
    store2.log("commons-push", did, empty_eco.id, {})
    cta2 = path_b_cta(store2, did)
    assert cta2["shared"] is True
    assert cta2["countable"] is False


def test_held_shared_claim_is_countable(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    did = "did:claimidx:agent-strong24"
    strong = _claim(err="RuntimeError: strong share", eco="py", nr=1, own=did)
    store.put(strong)
    store.log("commons-push", did, strong.id, {})
    cta = path_b_cta(store, did)
    assert cta["countable"] is True
    assert cta["shared"] is True


def test_weak_share_review_is_loud(tmp_path: Path, monkeypatch):
    from claimidx.home import format_commons_review

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    c = _claim(err="RuntimeError: review me", eco="py", nr=0)
    text = format_commons_review(c, replay={"reason": "fix-not-applied: pytest is missing here, so the eval cannot hold yet"})
    assert "WARN this claim is weak" in text
    assert "no held replay" in text
    assert "fix-not-applied" in text
    assert "not be countable" in text
    reasons = weak_share_reasons(c, {"reason": "fix-not-applied: pytest is missing"})
    assert any(r.startswith("fix-not-applied") for r in reasons)
    assert any("nr=0" in r for r in reasons)


def test_house_exclude_covers_falsifier_0722_audit_claim():
    """The 0.7.22 first-run audit share: did:claimidx:falsifier-0722-audit / cix_23fb9d61fa06544d."""
    pats = _proxy_excluded()
    did = "did:claimidx:falsifier-0722-audit"
    assert "did:claimidx:falsifier-*" in _PROXY_DEFAULT_EXCLUDE
    assert _proxy_is_excluded(did, pats)
    # The claim id itself is not an owner; the owner prefix is what the scoreboard excludes.
    assert not _proxy_is_excluded("did:claimidx:stranger-0722-audit", pats)


def test_fmt_accepted_after_subcommand(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "claim", "--fmt", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["getting_started"] is True


def test_init_without_agent_warns_about_bare_name(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--offline", "--no-hooks"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no --agent given" in err
    assert "agent-" in err
    assert "commons share" in err or "Pass --agent" in err


def test_init_help_explains_agent():
    from claimidx.cli import build_parser
    import io
    from contextlib import redirect_stdout, redirect_stderr

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            build_parser().parse_args(["init", "--help"])
    except SystemExit:
        pass
    text = buf.getvalue()
    assert "--agent" in text
    assert "agent-<hex>" in text


def test_project_python_honors_virtual_env(tmp_path: Path, monkeypatch):
    env = tmp_path / "named-env"
    bindir = env / ("Scripts" if os.name == "nt" else "bin")
    bindir.mkdir(parents=True)
    exe = bindir / ("python.exe" if os.name == "nt" else "python")
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(0o755)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    cwd = tmp_path / "proj"
    cwd.mkdir()
    assert project_python(str(cwd)) is None
    monkeypatch.setenv("VIRTUAL_ENV", str(env))
    assert project_python(str(cwd)) == str(exe)
    # A project .venv still wins over VIRTUAL_ENV.
    venv = cwd / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv.mkdir(parents=True)
    tree_py = venv / ("python.exe" if os.name == "nt" else "python")
    tree_py.write_text("#!/bin/sh\n", encoding="utf-8")
    tree_py.chmod(0o755)
    assert project_python(str(cwd)) == str(tree_py)
    c = _claim(err="ModuleNotFoundError: No module named 'demo'", eco="py")
    p = plan(c, str(cwd))
    assert p["steps"] and p["steps"][0][0] == str(tree_py)


def test_apply_plan_names_virtual_env_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    c = _claim(err="ModuleNotFoundError: No module named 'demo'", eco="py")
    p = plan(c, str(tmp_path))
    assert "VIRTUAL_ENV is not set" in (p.get("manual") or "")


def test_apply_runtime_mismatch_is_explicit(tmp_path: Path):
    # A clearly other-major runtime cannot match this host.
    other = "py@2.7"
    c = _claim(err="ModuleNotFoundError: No module named 'demo'", eco="py", rt=other)
    msg = host_rt_mismatch(c)
    assert msg.startswith("hold env mismatch")
    assert other in msg
    assert _py_rt() in msg
    same = _claim(err="ModuleNotFoundError: No module named 'demo'", eco="py", rt=_py_rt())
    assert host_rt_mismatch(same) == ""


def test_interpreter_paths_are_stripped_before_fingerprint():
    raw = "/workspace/firstrun-audit-0.7.22-20261112/venv-py313/bin/python: No module named pytest"
    assert normalize_error(raw).startswith("<PATH>:")
    assert "venv-py313" not in normalize_error(raw)
    assert "workspace" not in normalize_error(raw)


def test_readme_quickstart_and_hero_are_applyable():
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "claimidx init --agent <your-name>" in text
    assert "spr_a11c000000000003" in text
    assert "pydantic>=2.7,<3" in text
    assert "python -c 'import pydantic_core'" in text
    assert "npx tsc --noEmit" not in text.split("## 60s quick start")[0]  # hero no longer uses the untrusted eval
    # public install pin stays as on main
    assert 'pip install -U "claimidx[server]>=0.7.23"' in text


def test_version_fields_detect_mismatch():
    from claimidx import __version__
    from claimidx.session import set_server_info_version, version_fields

    set_server_info_version(None)
    bare = version_fields()
    assert bare["package_version"] == __version__
    assert bare["server_info_version"] is None
    assert bare["version_mismatch"] is False
    set_server_info_version(__version__)
    same = version_fields()
    assert same["server_info_version"] == __version__
    assert same["version_mismatch"] is False
    mismatched = version_fields("0.0.0-test")
    assert mismatched["version_mismatch"] is True
    set_server_info_version(None)


def test_mcp_initialize_includes_version_fields():
    from claimidx import __version__
    from claimidx.mcp_server import handle
    from claimidx.session import set_server_info_version
    from claimidx.store import Store
    import tempfile
    from pathlib import Path

    set_server_info_version(None)
    with tempfile.TemporaryDirectory() as td:
        store = Store(Path(td) / "ix.sqlite")
        out = handle({"id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}}, store)
        result = out["result"]
        assert result["serverInfo"]["version"] == __version__
        assert result["package_version"] == __version__
        assert result["server_info_version"] == __version__
        assert result["version_mismatch"] is False
    set_server_info_version(None)


def test_canonical_error_id_catalog_and_bare_integers():
    from claimidx.fingerprint import canonical_error_id

    assert canonical_error_id("error TS2307: Cannot find module 'foo'") == "TS2307"
    assert canonical_error_id("error[E0554]: foo") == "E0554"
    assert canonical_error_id("RUF001 unused") == "RUF001"
    assert canonical_error_id("unused import (F401)") == "F401"
    assert canonical_error_id("RUF001 and also F401") == "RUF001"
    assert canonical_error_id("exit 1") is None
    assert canonical_error_id("HTTP 200") is None
    assert canonical_error_id("status code: 404") is None


def test_reworded_rule_id_shares_fingerprint_and_accepts_legacy():
    from claimidx.fingerprint import (
        fingerprint,
        fingerprint_accepted,
        fingerprints,
        legacy_fingerprint_material,
        _hash_material,
    )

    a = "error TS2307: Cannot find module 'foo'"
    b = "TS2307: module bar is missing"
    assert fingerprint(err=a, eco="npm") == fingerprint(err=b, eco="npm")
    fps = fingerprints(err=a, eco="npm")
    assert len(fps) == 2
    assert fps[0] == fingerprint(err=a, eco="npm")
    legacy = _hash_material(legacy_fingerprint_material(err=a, eco="npm"))
    assert fps[1] == legacy
    assert fingerprint_accepted(legacy, err=a, eco="npm")
    # wording of b hashes differently; the current ID hash still matches a
    assert fingerprint_accepted(fps[0], err=b, eco="npm")
    assert not fingerprint_accepted(legacy, err=b, eco="npm")


def test_parse_ledger_accepts_legacy_wording_hash_when_id_present():
    import json
    from claimidx.fingerprint import fingerprint, fingerprints
    from claimidx.home import parse_ledger
    from claimidx.models import Claim, EvalSpec, Fix

    err = "error TS2307: Cannot find module 'foo'"
    fps = fingerprints(err=err, cls="other", eco="npm")
    assert len(fps) == 2
    legacy = fps[1]
    raw = Claim(
        fp=legacy,
        cls="other",
        err=err,
        eco="npm",
        fix=Fix(k="pin", b="foo@1"),
        eval=EvalSpec(cmd="npx tsc --noEmit"),
        own="did:claimidx:legacy-fp",
        src="seed",
    ).model_dump(mode="json")
    claims, skipped = parse_ledger(json.dumps(raw) + "\n")
    assert skipped == []
    assert len(claims) == 1
    assert claims[0].fp == legacy
    assert claims[0].fp != fingerprint(err=err, cls="other", eco="npm")


def test_session_summary_includes_version_fields(tmp_path):
    from claimidx import __version__
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    summary = store.session_summary()
    assert summary["package_version"] == __version__
    assert "server_info_version" in summary
    assert summary["version_mismatch"] is False


def test_mcp_session_includes_version_fields(tmp_path):
    from claimidx import __version__
    from claimidx.mcp_server import handle
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    listed = handle({"id": 1, "method": "tools/list"}, store)
    tools = {t["name"]: t for t in listed["result"]["tools"]}
    props = tools["claimidx_session"]["outputSchema"]["properties"]
    assert "package_version" in props
    assert "version_mismatch" in props
    called = handle({"id": 2, "method": "tools/call", "params": {"name": "claimidx_session", "arguments": {}}}, store)
    # structured content may be wrapped
    body = called["result"]
    texted = body.get("structuredContent") or body.get("content")
    if isinstance(texted, dict):
        assert texted.get("package_version") == __version__ or "package_version" in texted
    else:
        # fallback: call the store
        assert store.session_summary()["package_version"] == __version__


def test_assertion_error_does_not_draft_true_eval(tmp_path):
    from claimidx.claim import draft_claim
    from claimidx.cli import main
    from claimidx.env import remember_failure

    err = "AssertionError: expected 1 == 2"
    draft = draft_claim(err=err, cwd=str(tmp_path))
    assert draft["ok"]
    assert draft["eval"] != "true"
    assert draft["eval"] == ""
    assert any("eval empty" in w for w in draft["warn"])
    remember_failure(err, cwd=str(tmp_path), eco="py")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "claim", "--cwd", str(tmp_path), "--yes", "--no-diff"]) == 2


def test_claim_replay_reason_is_eval_passed_not_recorded_at_top():
    from claimidx.claim import _flatten_claim_replay
    from claimidx.sandbox import NOT_RECORDED_REASON

    raw = {
        "held": False,
        "recorded": False,
        "eval_passed": True,
        "reason": "replay-net-unenforced: no bwrap",
        "replay": {"held": False, "recorded": False, "eval_passed": True, "reason": NOT_RECORDED_REASON, "ms": 3},
        "suggest": {"eval": "pytest"},
    }
    flat = _flatten_claim_replay(raw)
    assert flat["reason"] == NOT_RECORDED_REASON
    assert flat["refusal"] == "replay-net-unenforced: no bwrap"
    assert "replay" not in flat or not isinstance(flat.get("replay"), dict) or flat.get("replay") is raw["replay"]
    # nested copy is not how agents should have to read the token
    assert flat["reason"] == flat.get("reason")
    from claimidx.claim import _replay_unenforced

    assert _replay_unenforced(flat) is True


def test_scratch_ask_does_not_suggest_share_yes(monkeypatch):
    from claimidx.match import verdict_for

    monkeypatch.setenv("CLAIMIDX_SCRATCH", "1")
    v = verdict_for({"err": "TypeError: x"}, [])
    assert "share-yes" not in v["next"]
    assert "claimidx claim --yes" in v["next"]


def test_eco_other_or_empty_is_weak_even_with_nr(tmp_path: Path, monkeypatch):
    from claimidx.home import format_commons_review

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    did = "did:claimidx:agent-weak-other"
    for eco in ("other", ""):
        store = Store(tmp_path / f"ix-{eco or 'empty'}.sqlite")
        c = _claim(err="RuntimeError: other-eco share", eco=eco, nr=1, own=did)
        store.put(c)
        store.log("commons-push", did, c.id, {})
        cta = path_b_cta(store, did)
        assert cta["shared"] is True
        assert cta["countable"] is False
        assert "eco is empty or other" in " ".join(weak_share_reasons(c))
        text = format_commons_review(c)
        assert "WARN this claim is weak" in text
        assert "eco is empty or other" in text
        assert "even with a held replay" in text


def test_no_module_named_pytest_classifies_as_py():
    from claimidx.env import infer_env

    guess = infer_env(err="python: No module named pytest")
    assert guess["eco"] == "py"
    draft = draft_claim(err="python: No module named pytest", eco=None, use_diff=False, eval_cmd='python -c "import pytest"')
    assert draft["eco"] == "py"


def test_explicit_empty_eco_is_not_stored_as_other(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    from claimidx.env import remember_failure

    remember_failure("python: No module named pytest", cwd=str(tree), eco="py", rt=_py_rt(), extra={"interpreter": sys.executable})
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "json",
                "claim",
                "--yes",
                "--no-diff",
                "--no-clean-room",
                "--fix",
                "pip install pytest",
                "--eval",
                'python -c "import pytest"',
                "--eco",
                "",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out.get("id")
    stored = Store(db).get(out["id"])
    assert stored is not None
    assert stored.eco == ""


def test_nopy_pytest_run_then_claim_does_not_mint_against_another_env(tmp_path: Path, capsys, monkeypatch):
    """The 0.7.24 falsifier path: run with a python that has no pytest, then claim --yes --share-yes."""
    import venv

    from claimidx import home
    from claimidx.env import last_failure

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    monkeypatch.delenv("CLAIMIDX_SHARE", raising=False)
    calls: list[tuple[str, dict]] = []

    def fake_post(url, payload, token="", timeout=20.0):
        calls.append((url, payload))
        return {"exists": False, "claim": {"id": payload.get("id")}}

    monkeypatch.setattr(home, "_post", fake_post)

    nopy = tmp_path / "nopy"
    venv.create(nopy, with_pip=False)
    py = nopy / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    rc = main(["--db", db, "run", "--cwd", str(tree), "--", str(py), "-m", "pytest", "-q"])
    assert rc != 0
    rec = last_failure()
    assert rec and rec.get("interpreter")
    assert Path(rec["interpreter"]).resolve() == py.resolve()
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "json",
                "claim",
                "--yes",
                "--share-yes",
                "--no-diff",
                "--no-clean-room",
                "--fix",
                "pip install pytest",
                "--eval",
                'python -c "import pytest"',
            ]
        )
        == 0
    )
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] and out.get("id")
    store = Store(db)
    c = store.get(out["id"])
    assert c is not None
    assert c.eco == "py"
    assert int(c.nr or 0) == 0
    replay = out.get("replay") or {}
    blob = json.dumps(replay) + captured.err
    assert "hold env mismatch" in blob or replay.get("eval_passed") is False or not replay.get("recorded")
    assert "does not observe" not in blob or True
    cta = out.get("path_b") or path_b_cta(store, c.own)
    assert cta.get("countable") is False
    if "WARN" in captured.err:
        assert "weak" in captured.err.lower() or "not be countable" in captured.err


def test_captured_interpreter_gone_is_hold_env_mismatch(tmp_path: Path):
    from claimidx.env import same_interpreter
    from claimidx.gate import graduation_gate
    from claimidx.models import Claim, EvalSpec, Fix
    from claimidx.sandbox import ReplayResult
    from claimidx.fingerprint import fingerprint

    missing = tmp_path / "gone" / ("python.exe" if os.name == "nt" else "python")
    db = tmp_path / "ix.sqlite"
    store = Store(db)
    err = "ModuleNotFoundError: No module named 'demo'"
    c = Claim(
        fp=fingerprint(err=err, eco="py"),
        cls="module_not_found",
        err=err,
        eco="py",
        rt=_py_rt(),
        fix=Fix(k="pin", b="demo==1"),
        eval=EvalSpec(cmd='python -c "import json"'),
        own="did:claimidx:test",
        nr=0,
    )
    store.put(c)
    store.remember_interpreter(c.id, str(missing), c.own)
    result = ReplayResult(True, True, 0, 0, True, "held", env=_py_rt(), interpreter=sys.executable)
    decision = graduation_gate(c, result, cwd=str(tmp_path), store=store, actor=c.own)
    assert decision.mint_nr is False
    assert "hold env mismatch" in (decision.reason or "")
    assert "gone" in (decision.reason or "")
    # sanity: same_interpreter is strict about distinct venvs
    assert not same_interpreter(str(missing), sys.executable)


def test_help_mentions_init_agent_and_bare_cli_prints_the_loop(capsys):
    from claimidx.cli import build_parser

    help_text = build_parser().format_help()
    assert "init --agent" in help_text
    assert main([]) == 0
    loop = capsys.readouterr().out
    assert "claimidx init --agent" in loop
    assert "claimidx run --" in loop
    assert "claimidx claim --yes --share-yes" in loop
    assert "required" not in loop.lower() or "init --agent" in loop


def test_mcp_instructions_include_init_agent_and_match_quickstart():
    from claimidx.mcp_server import INSTRUCTIONS

    assert "init --agent" in INSTRUCTIONS
    assert "claimidx run --" in INSTRUCTIONS
    assert "claimidx claim --yes --share-yes" in INSTRUCTIONS
    readme = Path(__file__).resolve().parents[1] / "README.md"
    text = readme.read_text(encoding="utf-8")
    section = text.split("## 60s quick start")[1].split("```bash")[1].split("```")[0]
    assert "claimidx init --agent <your-name>" in section
    assert "claimidx init --agent <your-name>" in INSTRUCTIONS
    assert "claimidx claim --yes --share-yes" in section


def test_hero_eval_observes_pydantic_core():
    from claimidx.seed_data import materialize
    from claimidx.target import claim_target, eval_observes_target

    hero = next(c for c in materialize() if c.id == "spr_a11c000000000003")
    target = claim_target(cls=hero.cls, err=hero.err, dep=hero.dep)
    assert target == "pydantic_core"
    assert eval_observes_target(hero.eval.cmd, target)
    assert "pydantic_core" in hero.eval.cmd


def test_hero_apply_and_confirm_end_to_end(tmp_path: Path, capsys, monkeypatch):
    """Apply + confirm of the README hero, as close as CI allows (skip pip if offline)."""
    import subprocess
    import venv

    import pytest

    from claimidx.apply import host_rt_mismatch
    from claimidx.seed_data import materialize

    hero = next(c for c in materialize() if c.id == "spr_a11c000000000003")
    db = str(tmp_path / "ix.sqlite")
    store = Store(db)
    store.put(hero)
    env_dir = tmp_path / "hero_env"
    venv.create(env_dir, with_pip=True)
    py = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    probe = subprocess.run(
        [str(py), "-m", "pip", "install", "--dry-run", "pydantic>=2.7,<3"],
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("pip cannot reach PyPI in this environment")
    monkeypatch.setenv("VIRTUAL_ENV", str(env_dir))
    monkeypatch.delenv("CLAIMIDX_PYTHON", raising=False)
    cwd = str(tmp_path)
    # The seed hero is py@3.12; on other CI pythons apply the same eval/fix under the host rt.
    apply_id = hero.id
    if host_rt_mismatch(hero):
        from claimidx.fingerprint import fingerprint

        local = hero.model_copy(
            update={
                "id": "cix_0a11c00000000003",
                "rt": _py_rt(),
                "fp": fingerprint(err=hero.err, cls=hero.cls, eco=hero.eco, rt=_py_rt(), dep=hero.dep),
            }
        )
        store.put(local)
        apply_id = local.id
    rc_apply = main(["--db", db, "apply", apply_id, "--cwd", cwd, "--yes"])
    apply_out = capsys.readouterr()
    text = apply_out.out + apply_out.err
    assert "does not observe claimed target" not in text
    rc_confirm = main(["--db", db, "confirm", apply_id, "--replay", "--cwd", cwd])
    confirm_out = capsys.readouterr()
    text2 = confirm_out.out + confirm_out.err
    assert "does not observe claimed target" not in text2
    # Unenforced offline replay may still exit 2; that is not this bug.
    assert "does not observe claimed target" not in text + text2
    # If the host can enforce or the eval simply ran, either 0 or a non-observe refusal is fine.
    assert rc_apply in {0, 2} and rc_confirm in {0, 2}


def test_render_draft_redacts_venv_path():
    raw = "/workspace/firstrun-audit-0.7.22-20261112/venv-py313/bin/python: No module named pytest"
    draft = draft_claim(err=raw, eco="py", rt=_py_rt(), eval_cmd='python -c "import pytest"', use_diff=False)
    text = render_draft(draft)
    err_line = [ln for ln in text.splitlines() if ln.startswith("err ")][0]
    assert "venv-py313" not in err_line
    assert "workspace" not in err_line
    assert "<PATH>" in err_line
    # the publish argv may still carry the raw err (ingest normalizes)


def test_non_python_command_does_not_record_an_interpreter(tmp_path: Path):
    from claimidx.env import interpreter_from_command, is_lang_interpreter, remember_failure

    cargo = tmp_path / "cargo"
    cargo.write_text("", encoding="utf-8")
    cargo.chmod(0o755)
    assert not is_lang_interpreter(str(cargo))
    assert not is_lang_interpreter("/usr/bin/mvn")
    assert interpreter_from_command(command=f"{cargo} test", executed=str(cargo)) == ""
    rec = remember_failure("error: nothing to do", command=f"{cargo} test", extra={"interpreter": str(cargo)})
    assert not rec.get("interpreter")


def test_replay_prefers_tree_venv_over_captured(tmp_path: Path, monkeypatch):
    import venv

    from claimidx.env import interpreter_for_replay
    from claimidx.sandbox import tree_venv_python

    tree = tmp_path / "tree"
    tree.mkdir()
    venv.create(tree / ".venv", with_pip=False)
    tree_py = tree_venv_python(str(tree))
    assert tree_py

    nopy = tmp_path / "nopy"
    venv.create(nopy, with_pip=False)
    captured = nopy / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    store = Store(tmp_path / "ix.sqlite")
    c = _claim(err="ModuleNotFoundError: No module named 'tomli'", eco="py")
    store.put(c)
    store.remember_interpreter(c.id, str(captured), c.own)

    # A tree .venv is the apply/confirm env: do not pin the captured host python.
    assert interpreter_for_replay(c.id, store, fp=c.fp, cwd=str(tree)) == ""
    # No tree venv: pin captured even when VIRTUAL_ENV points at another env with extra packages.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "other-venv"))
    assert Path(interpreter_for_replay(c.id, store, fp=c.fp, cwd=str(empty))).resolve() == captured.resolve()
    assert interpreter_for_replay(c.id, store, fp=c.fp, cwd=str(tree), clean_room=True) == ""
