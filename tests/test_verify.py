from pathlib import Path

from claimidx.cli import main
from claimidx.sandbox import ReplayResult
from claimidx.store import Store
from claimidx.verify import (
    _dep_pip,
    _install_plan,
    _pin_spec,
    decide,
    harness,
    is_harnessable,
    is_runnable,
    pick,
    run,
)
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.fingerprint import fingerprint, classify, normalize_error


def _claim(err: str, eval_cmd: str, *, fix_k="pin", fix_b="demo==1", st="proposed") -> Claim:
    cls = classify(err)
    return Claim(
        fp=fingerprint(err=err, cls=cls, eco="py", rt="py@3.11", dep=["demo@1"]),
        cls=cls,
        err=normalize_error(err),
        eco="py",
        rt="py@3.11",
        dep=["demo@1"],
        fix=Fix(k=fix_k, b=fix_b),
        eval=EvalSpec(cmd=eval_cmd),
        st=st,
        own="did:claimidx:test",
    )


def test_harness_confirms_when_eval_discriminates(tmp_path: Path, monkeypatch):
    replays = iter(
        [
            ReplayResult(True, True, 1, 0, False, "eval-miss"),
            ReplayResult(True, True, 0, 0, True, "held"),
        ]
    )
    monkeypatch.setattr("claimidx.verify.replay", lambda *a, **k: next(replays))

    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("claimidx.verify.subprocess.run", lambda *a, **k: R())
    c = _claim("ModuleNotFoundError: No module named 'demo'", "python -c \"import demo\"", fix_k="pin", fix_b="demo<2")
    d = harness(c, tmp_path / "h")
    assert d["action"] == "confirm"
    assert d["reason"] == "harness-discriminates"


def test_harness_skips_when_eval_holds_without_pin(tmp_path: Path, monkeypatch):
    replays = iter(
        [
            ReplayResult(True, True, 0, 0, True, "held"),
            ReplayResult(True, True, 0, 0, True, "held"),
        ]
    )
    monkeypatch.setattr("claimidx.verify.replay", lambda *a, **k: next(replays))

    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("claimidx.verify.subprocess.run", lambda *a, **k: R())
    c = _claim("ModuleNotFoundError: No module named 'demo'", "python -c \"import demo\"", fix_k="pin", fix_b="demo<2")
    d = harness(c, tmp_path / "h")
    assert d["action"] == "skip"
    assert d["reason"] == "harness-no-discriminate"


def test_harness_skips_without_pin(tmp_path: Path):
    c = _claim("TypeError: x", "python -c pass", fix_k="patch", fix_b="await x")
    d = harness(c, tmp_path / "h")
    assert d["action"] == "skip"
    assert d["reason"] == "harness-no-repro"


def test_harness_fails_when_pin_misses_targeted_eval(tmp_path: Path, monkeypatch):
    replays = iter(
        [
            ReplayResult(True, True, 1, 0, False, "eval-miss"),
            ReplayResult(True, True, 1, 0, False, "eval-miss"),
        ]
    )
    monkeypatch.setattr("claimidx.verify.replay", lambda *a, **k: next(replays))

    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("claimidx.verify.subprocess.run", lambda *a, **k: R())
    c = _claim(
        "ModuleNotFoundError: No module named 'pandas.util.testing'",
        "python -c \"from pandas.util.testing import assert_frame_equal\"",
        fix_k="pin",
        fix_b="pandas<2.0 and numpy<2",
    )
    d = harness(c, tmp_path / "h")
    assert d["action"] == "fail"
    assert d["reason"] == "harness-eval-miss"
    assert "pandas<2.0" in d["applied"]


def test_harness_skips_when_eval_does_not_target_pin(tmp_path: Path, monkeypatch):
    replays = iter(
        [
            ReplayResult(True, True, 1, 0, False, "eval-miss"),
            ReplayResult(True, True, 1, 0, False, "eval-miss"),
        ]
    )
    monkeypatch.setattr("claimidx.verify.replay", lambda *a, **k: next(replays))

    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("claimidx.verify.subprocess.run", lambda *a, **k: R())
    c = _claim(
        "ImportError: cannot import name Undefined",
        "python3 -c \"import app\"",
        fix_k="pin",
        fix_b="fastapi>=0.100.0",
    )
    d = harness(c, tmp_path / "h")
    assert d["action"] == "skip"
    assert d["reason"] == "harness-no-repro"


def test_harness_fails_on_pin_regression(tmp_path: Path, monkeypatch):
    replays = iter(
        [
            ReplayResult(True, True, 0, 0, True, "held"),
            ReplayResult(True, True, 1, 0, False, "eval-miss"),
        ]
    )
    monkeypatch.setattr("claimidx.verify.replay", lambda *a, **k: next(replays))

    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr("claimidx.verify.subprocess.run", lambda *a, **k: R())
    c = _claim("ModuleNotFoundError: No module named 'demo'", "python -c \"import demo\"", fix_k="pin", fix_b="demo==9")
    d = harness(c, tmp_path / "h")
    assert d["action"] == "fail"
    assert d["reason"] == "harness-eval-miss"


def test_harness_skips_broken_install(tmp_path: Path, monkeypatch):
    n = {"i": 0}

    class R:
        def __init__(self, rc):
            self.returncode = rc
            self.stderr = "could not find a version"
            self.stdout = ""

    def fake_run(*a, **k):
        n["i"] += 1
        if n["i"] == 1:
            return R(0)
        return R(1)

    monkeypatch.setattr("claimidx.verify.subprocess.run", fake_run)
    c = _claim("ModuleNotFoundError: No module named 'demo'", "python -c \"import demo\"", fix_k="pin", fix_b="demo<2")
    d = harness(c, tmp_path / "h")
    assert d["action"] == "skip"
    assert d["reason"] == "harness-broken-install"


def test_pin_spec_takes_first_requirement():
    assert _pin_spec("setuptools<81") == "setuptools<81"
    assert _pin_spec('numpy<2  (scipy 1.10.1 declares numpy<1.27)') == "numpy<2"
    assert _pin_spec("pip install setuptools") == "setuptools"
    assert _pin_spec("pip install standard-imghdr  # PEP 594") == "standard-imghdr"
    assert _pin_spec("pandas<2.0 and numpy<2  (both pins are required)") == "pandas<2.0"
    assert _pin_spec("markupsafe 2.1+ removed soft_unicode. Pin markupsafe<2.1 for Jinja2<3.1") == "markupsafe<2.1"


def test_dep_pip_and_install_plan_overlay_pin():
    assert _dep_pip(["transformers@4.38.2", "huggingface-hub@1.29.0"]) == [
        "transformers==4.38.2",
        "huggingface-hub==1.29.0",
    ]
    assert _dep_pip(["github.com/ugorji/go@v1.1.4"]) == []
    broken, fixed = _install_plan(
        ["huggingface_hub<1.0"],
        ["transformers@4.38.2", "huggingface-hub@1.29.0"],
    )
    assert "transformers==4.38.2" in broken
    assert "huggingface-hub==1.29.0" in broken
    assert "huggingface_hub<1.0" in fixed
    assert "huggingface-hub==1.29.0" not in fixed


def test_install_plan_empty_dep_broken_is_unpinned():
    """Unpinned then pin: empty dep must not pip-install the pin package as 'broken'."""
    broken, fixed = _install_plan(["standard-imghdr"], [])
    assert broken == []
    assert fixed == ["standard-imghdr"]
    broken, fixed = _install_plan(["pydantic>=2.6"], [])
    assert broken == []
    assert "pydantic>=2.6" in fixed


def test_harness_installs_dep_then_pin(tmp_path: Path, monkeypatch):
    calls = []
    replays = iter(
        [
            ReplayResult(True, True, 1, 0, False, "eval-miss"),
            ReplayResult(True, True, 0, 0, True, "held"),
        ]
    )
    monkeypatch.setattr("claimidx.verify.replay", lambda *a, **k: next(replays))

    class R:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(argv, *a, **k):
        calls.append(list(argv))
        return R()

    monkeypatch.setattr("claimidx.verify.subprocess.run", fake_run)
    c = _claim(
        "ImportError: huggingface-hub is required",
        "python -c \"import transformers\"",
        fix_k="pin",
        fix_b="huggingface_hub<1.0",
    )
    c.dep = ["transformers@4.38.2", "huggingface-hub@1.29.0"]
    d = harness(c, tmp_path / "h")
    assert d["action"] == "confirm"
    assert d["reason"] == "harness-discriminates"
    pip_calls = [x for x in calls if "-m" in x and "pip" in x]
    assert any("transformers==4.38.2" in x for x in pip_calls)
    assert any("huggingface_hub<1.0" in x for x in pip_calls)


def test_pick_runnable_only_self_contained_python():
    a = _claim("ModuleNotFoundError: No module named 'va'", "true")
    b = _claim("ModuleNotFoundError: No module named 'vb'", "python -c pass")
    g = _claim("missing go.sum entry", "go build ./...", fix_k="cmd", fix_b="go mod tidy")
    g.eco = "go"
    got = pick([a, b, g], k=8, ids=None, seen=set(), runnable=True)
    assert [x.err for x in got] == [b.err]
    assert is_runnable(b) and not is_runnable(a) and not is_runnable(g)


def test_pick_harness_only_pin_specs():
    pin = _claim("ModuleNotFoundError: No module named 'demo'", "python -c \"import demo\"", fix_k="pin", fix_b="demo<2")
    patch = _claim("TypeError: x", "python -c \"import app\"", fix_k="patch", fix_b="await x")
    taut = _claim("ModuleNotFoundError: No module named 'va'", "true", fix_k="pin", fix_b="va==1")
    got = pick([pin, patch, taut], k=8, ids=None, seen=set(), harness_mode=True)
    assert [x.err for x in got] == [pin.err]
    assert is_harnessable(pin) and not is_harnessable(patch) and not is_harnessable(taut)


def test_run_harness_skip_does_not_mint_nf(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_VERIFY_SEEN", str(tmp_path / "seen.json"))
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:test")
    store = Store(tmp_path / "ix.sqlite")
    c = _claim("ModuleNotFoundError: No module named 'demo'", "python -c \"import demo\"", fix_k="pin", fix_b="demo<2")
    store.put(c)
    monkeypatch.setattr(
        "claimidx.verify.harness",
        lambda claim, work: {"action": "skip", "reason": "harness-no-discriminate", "id": claim.id},
    )
    report = run(store, k=1, ids=[c.id], harness_mode=True)
    assert report["counts"]["fail"] == 0
    assert report["counts"]["skip"] == 1
    shown = store.get(c.id)
    assert shown.nf == 0
    assert shown.st == "proposed"


def test_decide_runnable_python_miss_is_fail(tmp_path: Path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    c = _claim(
        "ModuleNotFoundError: No module named 'no_such_cix_mod_zzz'",
        "python -c \"import no_such_cix_mod_zzz\"",
        fix_k="patch",
        fix_b="install it",
    )
    d = decide(c, scratch=scratch)
    assert d["action"] == "fail"


def test_pick_skips_true_and_rejected():
    a = _claim("ModuleNotFoundError: No module named 'va'", "true")
    b = _claim("ModuleNotFoundError: No module named 'vb'", "python -c pass")
    c = _claim("ModuleNotFoundError: No module named 'vc'", "python -c pass", st="rejected")
    got = pick([a, b, c], k=8, ids=None, seen=set())
    assert [x.err for x in got] == [b.err]


def test_decide_skips_builtin_and_tree(tmp_path: Path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    t = _claim("ModuleNotFoundError: No module named 'vt'", "true")
    g = _claim("missing go.sum entry", "go build ./...", fix_k="cmd", fix_b="go mod tidy")
    g.eco = "go"
    assert decide(t, scratch=scratch)["action"] == "skip"
    d = decide(g, scratch=scratch)
    assert d["action"] == "skip"
    assert "precondition" in (d.get("reason") or "")


def test_decide_skips_pytest_without_tree(tmp_path: Path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    p = _claim("TypeError: x", "python3 -m pytest -q", fix_k="patch", fix_b="assert x")
    d = decide(p, scratch=scratch)
    assert d["action"] == "skip"
    assert "precondition" in (d.get("reason") or "") or "missing" in (d.get("reason") or "")


def test_decide_skips_node_spawn_composer_wrapper(tmp_path: Path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    cmd = "node -e \"process.exit(require('child_process').spawnSync('composer',['install'],{stdio:'inherit'}).status)\""
    c = _claim("Your requirements could not be resolved", cmd, fix_k="pin", fix_b="phpunit/phpunit")
    d = decide(c, scratch=scratch)
    assert d["action"] == "skip"
    assert "wrapper" in (d.get("reason") or "")


def test_decide_skips_node_spawn_cargo_wrapper(tmp_path: Path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    cmd = "node -e \"process.exit(require('child_process').spawnSync('cargo',['build'],{cwd:'app',stdio:'inherit'}).status)\""
    c = _claim("error: rustc is not supported", cmd, fix_k="config", fix_b="rust-version = 1.95.0")
    d = decide(c, scratch=scratch)
    assert d["action"] == "skip"
    assert "wrapper" in (d.get("reason") or "")


def test_decide_skips_version_only_eval(tmp_path: Path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    c = _claim("Error [ERR_PACKAGE_PATH_NOT_EXPORTED]", "node --version", fix_k="patch", fix_b="import from uuid")
    d = decide(c, scratch=scratch)
    assert d["action"] == "skip"
    assert d["reason"] == "tautology-eval"


def test_decide_skips_local_pip_without_pyproject(tmp_path: Path):
    scratch = tmp_path / "s"
    scratch.mkdir()
    c = _claim(
        "error: Multiple top-level packages discovered in a flat-layout",
        "python -m pip install --no-build-isolation -e .",
        fix_k="config",
        fix_b="[tool.setuptools.packages.find]",
    )
    d = decide(c, scratch=scratch)
    assert d["action"] == "skip"
    assert "precondition" in (d.get("reason") or "") or "missing" in (d.get("reason") or "")


def test_verify_confirms_python_c(tmp_path: Path, capsys, monkeypatch):
    import sys

    monkeypatch.setenv("CLAIMIDX_VERIFY_SEEN", str(tmp_path / "seen.json"))
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'vok'"
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
    assert main(["--db", db, "--fmt", "id", "publish", "--err", err, "--eco", "py", "--rt", rt, "--fix-k", "patch", "--fix-b", "pass", "--eval", "python -c pass"]) == 0
    cid = capsys.readouterr().out.strip()
    rc = main(["--db", db, "--fmt", "json", "verify", "--id", cid, "-k", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"confirm"' in out
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    shown = capsys.readouterr().out
    assert '"st": "confirmed"' in shown or '"nc": 1' in shown


def test_verify_help_dry_run_skips_execution(capsys):
    """Agents read --help. --dry-run must say it does not run evals/venv/pip."""
    import pytest

    with pytest.raises(SystemExit) as ei:
        main(["verify", "--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out.lower()
    assert "--dry-run" in out
    assert "venv" in out or "pip" in out
    assert "not run" in out or "do not run" in out or "without" in out


def test_verify_dry_run_does_not_write(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_VERIFY_SEEN", str(tmp_path / "seen.json"))
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'vdry'"
    import sys
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
    assert main(["--db", db, "--fmt", "id", "publish", "--err", err, "--eco", "py", "--rt", rt, "--fix-k", "patch", "--fix-b", "pass", "--eval", "python -c pass"]) == 0
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "verify", "--dry-run", "--id", cid]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    shown = capsys.readouterr().out
    assert '"nc": 0' in shown


def test_verify_dry_run_harness_does_not_create_venv(tmp_path: Path, capsys, monkeypatch):
    """--dry-run must not pip-install pins. harness venv+pip hung verify --dry-run -k 8 past 120s."""
    import json
    import subprocess
    import sys

    monkeypatch.setenv("CLAIMIDX_VERIFY_SEEN", str(tmp_path / "seen.json"))
    db = str(tmp_path / "ix.sqlite")
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
    err = "ModuleNotFoundError: No module named 'pydantic_core'"
    eval_cmd = 'python -c "import pydantic"'
    assert main([
        "--db", db, "--fmt", "id", "publish",
        "--err", err, "--eco", "py", "--rt", rt,
        "--fix-k", "pin", "--fix-b", "pydantic>=2.6",
        "--eval", eval_cmd,
    ]) == 0
    cid = capsys.readouterr().out.strip()
    calls: list[list] = []

    def _blocked(*args, **kwargs):
        argv = args[0] if args else kwargs.get("args")
        calls.append(list(argv) if argv else [])
        raise AssertionError(f"subprocess.run during dry-run: {argv}")

    monkeypatch.setattr(subprocess, "run", _blocked)
    monkeypatch.setattr("claimidx.verify.subprocess.run", _blocked)
    rc = main([
        "--db", db, "--fmt", "json", "verify", "--dry-run",
        "--runnable", "--harness", "--id", cid, "-k", "1",
    ])
    out = capsys.readouterr().out
    assert rc == 0, out
    report = json.loads(out)
    assert report["dry_run"] is True
    assert report["n"] == 1
    assert report["results"][0]["id"] == cid
    assert report["results"][0]["action"] == "skip"
    assert report["results"][0]["reason"] == "dry-run"
    assert calls == []
