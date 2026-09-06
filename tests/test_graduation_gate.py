"""Graduation gate contract: what `confirm --replay` may mint `nr` from.

Source: `Validated Results/` (excelsior colony mutation-canary, optional
observed-digest warn). These tests assert the DESIRED contract. Checks that
are not implemented yet are `xfail(strict=True)`: they must keep failing until
the closing step lands, and flipping one green is the definition of done for
that step.

- X1  eval that never observes the claimed target must not mint nr
- X2  eval artifact mutated after binding must not mint nr
- X2b tree recipe with no binding must not mint nr (strict)
- I1  observed_digest on a dep pin must warn `digest_drift` when local bytes change
"""

from __future__ import annotations

import hashlib
import json
import sys
import textwrap
from pathlib import Path

import pytest

from claimidx.cli import main
from claimidx.sandbox import ReplayResult

NOT_YET = "graduation gate: closing step not implemented yet"


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def _publish(db: str, capsys, *, err: str, eval_cmd: str, fix_k: str = "constraint", fix_b: str = "ok", extra: list[str] | None = None) -> str:
    args = [
        "--db",
        db,
        "--fmt",
        "id",
        "publish",
        "--err",
        err,
        "--eco",
        "py",
        "--rt",
        _py_rt(),
        "--fix-k",
        fix_k,
        "--fix-b",
        fix_b,
        "--eval",
        eval_cmd,
        *(extra or []),
    ]
    assert main(args) == 0
    return capsys.readouterr().out.strip()


def _confirm(db: str, capsys, cid: str, *, cwd: str | None = None) -> tuple[int, dict]:
    args = ["--db", db, "--fmt", "json", "confirm", "--replay"]
    if cwd:
        args += ["--cwd", cwd]
    rc = main(args + [cid])
    return rc, json.loads(capsys.readouterr().out)


def _nr(db: str, capsys, cid: str) -> int:
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    return int(json.loads(capsys.readouterr().out)["nr"])


def _write_check(tree: Path, *, honest: bool) -> None:
    if honest:
        (tree / "check.py").write_text(
            "from pathlib import Path\nimport sys\n"
            "m = Path(__file__).with_name('marker.txt')\n"
            "sys.exit(0 if m.read_text(encoding='utf-8').strip() == 'good' else 1)\n",
            encoding="utf-8",
        )
    else:
        (tree / "check.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Step 1: one choke point. These pass once the gate exists.
# --------------------------------------------------------------------------


def test_gate_module_exposes_decision():
    from claimidx.gate import GateDecision, graduation_gate

    d = GateDecision(mint_nr=True, reason="held", warns=[])
    assert d.as_tuple() == (True, "held")
    assert callable(graduation_gate)


def test_gate_keeps_env_check(tmp_path: Path):
    """The rt <-> observed env rule moved inside the gate unchanged."""
    from claimidx.gate import graduation_gate
    from claimidx.models import Claim, EvalSpec, Fix
    from claimidx.fingerprint import fingerprint

    err = "ModuleNotFoundError: No module named 'gate_env'"
    claim = Claim(
        fp=fingerprint(err=err, eco="py", rt="py@3.99"),
        cls="module_not_found",
        err=err,
        eco="py",
        rt="py@3.99",
        fix=Fix(k="constraint", b="ok"),
        eval=EvalSpec(cmd='python -c "import gate_env"'),
    )
    held = ReplayResult(True, True, 0, 0, True, "held", env=_py_rt())
    d = graduation_gate(claim, held, cwd=str(tmp_path))
    assert d.mint_nr is False
    assert "hold env mismatch" in d.reason


def test_gate_is_the_only_nr_path_in_cli_and_mcp():
    """No caller may bypass the gate to reach replay_records_hold."""
    import claimidx.cli as cli
    import claimidx.mcp_server as mcp
    import claimidx.replay as rp

    for mod in (cli, mcp, rp):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "replay_records_hold" not in src, mod.__name__
        assert "graduation_gate" in src, mod.__name__


# --------------------------------------------------------------------------
# X1 — target attribution
# --------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_YET)
def test_x1_confirm_replay_refuses_eval_that_ignores_claimed_target(tmp_path: Path, capsys):
    """Claim target is module 'bind_target'; eval print(1) never observes it."""
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(
        db,
        capsys,
        err="ModuleNotFoundError: No module named 'bind_target'",
        eval_cmd='python -c "print(1)"',
        fix_b="pip install bind-target",
    )
    rc, out = _confirm(db, capsys, cid)
    assert rc != 0, out
    assert out.get("recorded") is False
    assert "bind_target" in out.get("reason", "")
    assert _nr(db, capsys, cid) == 0


def test_x1_eval_naming_the_target_still_graduates(tmp_path: Path, capsys):
    """Sanity: an eval that imports the claimed module keeps minting nr."""
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(
        db,
        capsys,
        err="ModuleNotFoundError: No module named 'json'",
        eval_cmd='python -c "import json"',
    )
    rc, out = _confirm(db, capsys, cid)
    assert rc == 0, out
    assert _nr(db, capsys, cid) == 1


# --------------------------------------------------------------------------
# X2 — proof binding / mutation canary
# --------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_YET)
def test_x2_binding_at_publish_cwd_refuses_mutated_artifact(tmp_path: Path, capsys):
    """`publish --cwd` binds check.py; rewriting it before the first replay must refuse."""
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "marker.txt").write_text("good\n", encoding="utf-8")
    _write_check(tree, honest=True)
    cid = _publish(
        db,
        capsys,
        err="RuntimeError: marker contract broken for canary_mod",
        eval_cmd="python check.py",
        fix_k="patch",
        fix_b="restore marker.txt to good",
        extra=["--cwd", str(tree)],
    )
    _write_check(tree, honest=False)
    (tree / "marker.txt").write_text("evil\n", encoding="utf-8")
    rc, out = _confirm(db, capsys, cid, cwd=str(tree))
    assert rc != 0, out
    assert "proof-artifact-drift" in out.get("reason", ""), out
    assert _nr(db, capsys, cid) == 0


@pytest.mark.xfail(strict=True, reason=NOT_YET)
def test_x2_tofu_then_mutation_refuses(tmp_path: Path, capsys):
    """First held replay under --cwd binds (TOFU); a later mutated replay must refuse."""
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "marker.txt").write_text("good\n", encoding="utf-8")
    _write_check(tree, honest=True)
    cid = _publish(
        db,
        capsys,
        err="RuntimeError: marker contract broken for canary_mod",
        eval_cmd="python check.py",
        fix_k="patch",
        fix_b="restore marker.txt to good",
    )
    rc, out = _confirm(db, capsys, cid, cwd=str(tree))
    assert rc == 0, out
    assert _nr(db, capsys, cid) == 1

    _write_check(tree, honest=False)
    (tree / "marker.txt").write_text("evil\n", encoding="utf-8")
    rc, out = _confirm(db, capsys, cid, cwd=str(tree))
    assert rc != 0, out
    assert "proof-artifact-drift" in out.get("reason", ""), out
    assert _nr(db, capsys, cid) == 1


@pytest.mark.xfail(strict=True, reason=NOT_YET)
def test_x2b_unbound_tree_recipe_does_not_mint_nr(tmp_path: Path, capsys):
    """Strict: a tree-scoped recipe replayed without --cwd (nothing to bind) records a hold but no nr."""
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(
        db,
        capsys,
        err="RuntimeError: marker contract broken for canary_mod",
        eval_cmd="python check.py",
        fix_k="patch",
        fix_b="restore marker.txt to good",
    )
    rc, out = _confirm(db, capsys, cid)
    assert rc != 0, out
    assert "unbound-proof" in out.get("reason", ""), out
    assert _nr(db, capsys, cid) == 0


# --------------------------------------------------------------------------
# I1 — observed_digest on a dependency pin (warn only by default)
# --------------------------------------------------------------------------


def _write_pkg(root: Path, body: str) -> Path:
    pkg = root / "bytecanary"
    pkg.mkdir(parents=True, exist_ok=True)
    init = pkg / "__init__.py"
    init.write_text(
        textwrap.dedent(
            f"""\
            __version__ = "1.0.0"
            PAYLOAD = {body!r}
            """
        ),
        encoding="utf-8",
    )
    return init


@pytest.mark.xfail(strict=True, reason=NOT_YET)
def test_i1_observed_digest_warns_digest_drift_under_same_pin(tmp_path: Path, capsys):
    tree = tmp_path / "tree"
    tree.mkdir()
    init = _write_pkg(tree, "CLEAN_PAYLOAD_A")
    sha_a = hashlib.sha256(init.read_bytes()).hexdigest()

    db = str(tmp_path / "ix.sqlite")
    tree_esc = str(tree).replace("\\", "\\\\")
    eval_cmd = "python -c \"import sys; sys.path.insert(0, r'" + tree_esc + "'); import bytecanary as d; assert d.__version__=='1.0.0'\""
    cid = _publish(
        db,
        capsys,
        err="ModuleNotFoundError: No module named 'bytecanary'",
        eval_cmd=eval_cmd,
        fix_k="pin",
        fix_b="bytecanary==1.0.0",
        extra=["--dep", "bytecanary@1.0.0", "--observed-digest", f"bytecanary@1.0.0=sha256:{sha_a}"],
    )
    rc, out = _confirm(db, capsys, cid, cwd=str(tree))
    assert rc == 0, out
    assert "digest_drift" not in json.dumps(out)

    init = _write_pkg(tree, "MUTATED_PAYLOAD_B_SUPPLY_CHAIN")
    assert hashlib.sha256(init.read_bytes()).hexdigest() != sha_a

    rc, out = _confirm(db, capsys, cid, cwd=str(tree))
    # Default is warn-only: the hold still records, the drift is surfaced.
    assert "digest_drift" in json.dumps(out), out
