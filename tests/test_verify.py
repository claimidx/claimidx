from pathlib import Path

from claimidx.cli import main
from claimidx.verify import _pin_spec, decide, pick
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


def test_pin_spec_takes_first_requirement():
    assert _pin_spec("setuptools<81") == "setuptools<81"
    assert _pin_spec('numpy<2  (scipy 1.10.1 declares numpy<1.27)') == "numpy<2"
    assert _pin_spec("pip install setuptools") is None


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
