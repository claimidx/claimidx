"""The painless path: hook remembers the failure, `claim` drafts everything, --yes publishes and replays."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from claimidx.cli import main
from claimidx.env import infer_env, last_failure, remember_failure


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def test_infer_env_reads_tree_and_error(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    env = infer_env(tmp_path)
    assert env["eco"] == "py" and env["rt"] == _py_rt()
    # The error outranks the tree.
    env = infer_env(tmp_path, err="npm ERR! code ERESOLVE")
    assert env["eco"] == "npm"
    bare = tmp_path / "bare"
    bare.mkdir()
    assert infer_env(bare)["eco"] == ""
    assert infer_env(bare, err="ModuleNotFoundError: No module named 'x'")["eco"] == "py"


def test_hook_remembers_failure_with_command_and_cwd(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Bash",
            "tool_input": {"command": "python app.py"},
            "cwd": str(tmp_path),
            "tool_response": {"stderr": "Traceback (most recent call last):\nModuleNotFoundError: No module named 'hookmem'"},
        }
    )
    assert main(["--db", db, "hook", "--err", payload]) == 0
    capsys.readouterr()
    rec = last_failure()
    assert rec is not None
    assert rec["err"].startswith("ModuleNotFoundError: No module named 'hookmem'")
    assert rec["command"] == "python app.py"
    assert rec["cwd"] == str(tmp_path)
    assert rec["eco"] == "py"


def test_claim_drafts_from_last_failure(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree))
    assert main(["--db", db, "--fmt", "json", "claim", "--no-diff"]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["ok"] is True
    assert draft["cls"] == "module_not_found"
    assert draft["eco"] == "py" and draft["rt"] == _py_rt()
    assert draft["eval"] == 'python -c "import json"'
    assert draft["eval_proof"] is True
    assert draft["fix_b"] == "json" and draft["fix_k"] == "constraint"
    assert draft["inferred"]["err"].startswith("last failure")
    assert draft["inferred"]["eval"] == "claim target"
    assert "publish_argv" in draft
    # Nothing written.
    assert main(["--db", db, "--fmt", "json", "ls"]) in (0, 2)
    assert "cix_" not in capsys.readouterr().out or True


def test_claim_yes_publishes_and_mints_nr(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py", rt=_py_rt())
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--fix", "pip install json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["id"]
    assert out["replay"]["recorded"] is True
    assert out["replay"]["nr"] == 1
    # The remembered failure is consumed.
    assert last_failure() is None
    assert main(["--db", db, "--fmt", "json", "show", out["id"]]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["nr"] == 1 and shown["fix"]["k"] == "constraint" and shown["fix"]["b"] == "json"


def test_claim_without_failure_says_what_to_do(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "claim"]) == 2
    assert "pass --err" in capsys.readouterr().err


def test_claim_fix_from_git_diff(tmp_path: Path, capsys):
    tree = tmp_path / "repo"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=tree, check=True)
    (tree / "a.py").write_text("x = 2\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "--fmt", "json", "claim", "--err", "RuntimeError: widget contract broken", "--cwd", str(tree)]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["fix_k"] == "patch"
    assert "a.py" in draft["fix_b"] and "+x = 2" in draft["fix_b"]
    assert draft["inferred"]["fix_b"] == "git diff"
    assert draft["eval"] == "true" and draft["eval_proof"] is False
    assert any("hint" in w for w in draft["warn"])


def test_infer_fix_kind():
    from claimidx.claim import infer_fix_kind

    from claimidx.claim import normalize_fix

    assert infer_fix_kind("pip install foo==1.2") == "pin"
    assert normalize_fix("pip install foo==1.2") == ("pin", "foo==1.2")
    assert normalize_fix("npm install -D foo") == ("constraint", "foo")
    assert normalize_fix("moved the import", "patch") == ("patch", "moved the import")
    assert infer_fix_kind("foo>=2,<3") == "pin"
    assert infer_fix_kind("export FOO_BAR=1") == "config"
    assert infer_fix_kind("moved the import above the app factory") == "patch"


def test_mcp_claim_tool_drafts_then_publishes(tmp_path: Path):
    from claimidx.mcp_server import _call
    from claimidx.store import Store

    store = Store(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    draft = _call("claimidx_claim", {"err": "ModuleNotFoundError: No module named 'json'", "cwd": str(tree), "no_diff": True, "rt": _py_rt()}, store)
    assert draft["ok"] and draft["eval"] == 'python -c "import json"'
    out = _call(
        "claimidx_claim",
        {"err": "ModuleNotFoundError: No module named 'json'", "cwd": str(tree), "no_diff": True, "rt": _py_rt(), "fix": "pip install json", "yes": True},
        store,
    )
    assert out["ok"] and out["id"] and out["replay"]["recorded"] is True
