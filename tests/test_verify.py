from pathlib import Path

from claimidx.cli import main
from claimidx.sandbox import ReplayResult
from claimidx.store import Store
from claimidx.verify import _pin_spec, decide, harness, is_harnessable, is_runnable, pick, run
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
    monkeypatch.setenv("CLAIMIDX_VERIFY_SEEN", str(tmp_path / "seen.json"))
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'vok'"
    assert main(["--db", db, "--fmt", "id", "publish", "--err", err, "--eco", "py", "--fix-k", "patch", "--fix-b", "pass", "--eval", "python -c pass"]) == 0
    cid = capsys.readouterr().out.strip()
    rc = main(["--db", db, "--fmt", "json", "verify", "--id", cid, "-k", "1"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"confirm"' in out
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    shown = capsys.readouterr().out
    assert '"st": "confirmed"' in shown or '"nc": 1' in shown


def test_verify_dry_run_does_not_write(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_VERIFY_SEEN", str(tmp_path / "seen.json"))
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'vdry'"
    assert main(["--db", db, "--fmt", "id", "publish", "--err", err, "--eco", "py", "--fix-k", "patch", "--fix-b", "pass", "--eval", "python -c pass"]) == 0
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "verify", "--dry-run", "--id", cid]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    shown = capsys.readouterr().out
    assert '"nc": 0' in shown
