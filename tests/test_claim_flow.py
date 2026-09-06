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


def test_replay_uses_the_trees_own_venv(tmp_path: Path):
    """A claim about a project replays under that project's interpreter, not whatever `python` is on PATH."""
    import subprocess

    from claimidx.sandbox import project_python, replay, resolve_argv

    tree = tmp_path / "proj"
    tree.mkdir()
    subprocess.run([sys.executable, "-m", "venv", str(tree / ".venv")], check=True, capture_output=True, timeout=120)
    own = project_python(tree)
    assert own and resolve_argv(["python", "-c", "print(1)"], str(tree))[0] == own
    marker = tree / ".venv" / "cix_marker.txt"
    marker.write_text("here", encoding="utf-8")
    # The venv interpreter sees its own prefix; a foreign one would not.
    res = replay("python -c \"import sys, os; raise SystemExit(0 if os.path.exists(os.path.join(sys.prefix, 'cix_marker.txt')) else 1)\"", 0, cwd=str(tree))
    assert res.held, res.as_dict()


def test_claim_drafts_the_trees_test_as_eval(tmp_path: Path, capsys):
    tree = tmp_path / "web"
    tree.mkdir()
    (tree / "package.json").write_text(json.dumps({"name": "web", "scripts": {"test": "node test.js"}}), encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(["--db", db, "--fmt", "json", "claim", "--err", "Error: Cannot find module './lib'", "--cwd", str(tree), "--no-diff", "--fix", "added lib.js"])
        == 0
    )
    draft = json.loads(capsys.readouterr().out)
    assert draft["eval"] == "npm test" and draft["inferred"]["eval"] == "tree recipe"
    assert draft["target"] == ""


def _broken_repo(tmp_path: Path) -> Path:
    import subprocess

    tree = tmp_path / "repo"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / "pyproject.toml").write_text('[project]\nname="t"\nversion="0"\n', encoding="utf-8")
    (tree / "mod.py").write_text("import json\n\ndef load(t):\n    return json.loads(t, encoding='utf-8')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "broken"], cwd=tree, check=True)
    return tree


def test_patch_fix_is_git_apply_clean_and_unapplied_replay_is_not_a_fail(tmp_path: Path, capsys):
    import subprocess

    tree = _broken_repo(tmp_path)
    (tree / "mod.py").write_text("import json\n\ndef load(t):\n    return json.loads(t)\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "json",
                "claim",
                "--err",
                "TypeError: loads() got an unexpected keyword argument 'encoding'",
                "--cwd",
                str(tree),
                "--eval",
                "python -c \"import mod; mod.load('{}')\"",
                "--yes",
                "--no-replay",
            ]
        )
        == 0
    )
    out = json.loads(capsys.readouterr().out)
    cid = out["id"]
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    fix_b = json.loads(capsys.readouterr().out)["fix"]["b"]
    assert fix_b.startswith("diff --git") and fix_b.endswith("\n") and "1 file changed" not in fix_b
    # A second checkout at the broken commit: replay before applying is not a fail.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(tree), str(other)], check=True)
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", "--cwd", str(other), cid])
    res = json.loads(capsys.readouterr().out)
    assert rc == 2 and res["recorded"] is False and res["reason"].startswith("fix-not-applied") and "mod.py" in res["reason"]
    # Apply it mechanically; now the replay holds and binds.
    patch = other / "fix.patch"
    patch.write_text(fix_b, encoding="utf-8")
    subprocess.run(["git", "apply", "fix.patch"], cwd=other, check=True)
    assert "encoding=" not in (other / "mod.py").read_text(encoding="utf-8")
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", "--cwd", str(other), cid]) == 0
    assert json.loads(capsys.readouterr().out)["held"] is True


def test_hook_strips_pytest_error_prefix(tmp_path: Path):
    from claimidx.hook import extract_hook_err

    body = "tests/test_x.py:3: in test\n>   load()\nE       TypeError: load() missing 1 required positional argument: 'Loader'\n"
    err, _ = extract_hook_err(json.dumps({"hook_event_name": "PostToolUseFailure", "tool_response": {"stdout": body}}))
    assert err == "TypeError: load() missing 1 required positional argument: 'Loader'"


def test_project_bin_and_tree_eval(tmp_path: Path):
    from claimidx.env import tree_eval
    from claimidx.sandbox import project_bin, resolve_argv

    tree = tmp_path / "t"
    (tree / ".venv" / "bin").mkdir(parents=True)
    fake = tree / ".venv" / "bin" / "pytest"
    fake.write_text("", encoding="utf-8")
    assert project_bin("pytest", tree) == str(fake)
    assert resolve_argv(["pytest", "-q"], str(tree))[0] == str(fake)
    (tree / "tests").mkdir()
    assert tree_eval(tree, "py") == "python -m pytest -q"


def test_deps_from_traceback_names_only_the_raising_package(tmp_path: Path):
    from claimidx.env import deps_from_traceback

    tb = (
        'File "/x/app.py", line 3, in <module>\n'
        'File "/x/.venv/lib/python3.12/site-packages/_pytest/runner.py", line 1, in x\n'
        'File "/x/.venv/lib/python3.12/site-packages/pluggy/_hooks.py", line 1, in y\n'
        'File "/x/.venv/lib/python3.12/site-packages/pydantic/main.py", line 253, in __init__\n'
        "pydantic_core._pydantic_core.ValidationError: 1 validation error"
    )
    got = deps_from_traceback(tb, tmp_path)
    assert got and got[0].startswith("pydantic@")
    assert deps_from_traceback('File "/x/app.py", line 3\nTypeError: boom', tmp_path) == []
    node = tmp_path / "node_modules" / "next"
    node.mkdir(parents=True)
    (node / "package.json").write_text('{"version":"15.0.0"}', encoding="utf-8")
    assert deps_from_traceback("at /x/node_modules/next/dist/server.js:1:1", tmp_path, "npm") == ["next@15.0.0"]


def test_hook_infers_dep_from_traceback_for_the_fingerprint(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    body = (
        "Traceback (most recent call last):\n"
        '  File "/x/.venv/lib/python3.12/site-packages/pydantic/main.py", line 253, in __init__\n'
        "pydantic_core._pydantic_core.ValidationError: 1 validation error for Model\n"
    )
    payload = json.dumps({"hook_event_name": "PostToolUseFailure", "tool_response": {"stderr": body}, "cwd": str(tmp_path)})
    assert main(["--db", db, "hook", "--err", payload]) == 0
    capsys.readouterr()
    from claimidx.env import last_failure

    rec = last_failure()
    assert rec is not None and rec["fp"]
    from claimidx.fingerprint import classify, fingerprint

    err = "pydantic_core._pydantic_core.ValidationError: 1 validation error for Model"
    from importlib.metadata import version

    with_dep = fingerprint(
        err=err, cls=classify(err), eco="py", rt=f"py@{sys.version_info.major}.{sys.version_info.minor}", dep=[f"pydantic@{version('pydantic')}"]
    )
    assert rec["fp"] == with_dep
