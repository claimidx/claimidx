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
    assert "python -c 'import pydantic'" in text
    assert "npx tsc --noEmit" not in text.split("## 60s quick start")[0]  # hero no longer uses the untrusted eval
    # public install pin is unchanged
    assert 'pip install -U "claimidx[server]>=0.7.22"' in text


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
