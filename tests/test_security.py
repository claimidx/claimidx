import json
import pytest
from pydantic import ValidationError

from claimidx.models import Fix
from claimidx.security import SecretError, reject_secrets


def test_rejects_openai_key():
    with pytest.raises(SecretError):
        reject_secrets("sk-" + "a" * 24)


def test_claim_fix_body_secret_is_validation_error():
    with pytest.raises((SecretError, ValidationError)):
        Fix(k="cmd", b="export OPENAI_API_KEY=sk-" + "b" * 24)


def test_benign_text_passes():
    reject_secrets("TypeError: params is a Promise")
    reject_secrets("tools/list returned empty")


def test_jdk_storepass_changeit_is_not_a_secret():
    reject_secrets("keytool -importcert -storepass changeit -file ca.pem")


def test_bearer_scheme_without_token_is_not_a_secret():
    reject_secrets('WWW-Authenticate: Bearer realm="api"')


def test_bearer_token_still_rejected():
    with pytest.raises(SecretError):
        reject_secrets("Authorization: Bearer " + "a" * 24)


def test_strip_control_removes_ansi_and_bidi():
    from claimidx.security import strip_control

    assert strip_control("\x1b[31mred\x1b[0m ok‮ evil") == "red ok evil"
    assert strip_control("a\tb\nc\x00d") == "a\tb\ncd"


def test_claim_fields_are_stripped_of_terminal_escapes():
    from claimidx.fingerprint import fingerprint
    from claimidx.models import Claim, EvalSpec, Fix

    err = "RuntimeError: \x1b[2K\x1b[1Gall good‮"
    c = Claim(fp=fingerprint(err=err), cls="other", err=err, fix=Fix(k="patch", b="\x1b[31mrun this\x1b[0m"), eval=EvalSpec(cmd="true"))
    assert "\x1b" not in c.err and "‮" not in c.err and c.fix.b == "run this"


def test_last_failure_never_stores_a_secret_command(tmp_path, monkeypatch):
    from claimidx.env import last_failure, remember_failure

    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    remember_failure("RuntimeError: x", command='curl -H "Authorization: ' + "Bearer " + 'abcdefghijklmnopqrstuvwxyz0123" https://api')
    assert last_failure()["command"] == ""
    remember_failure("RuntimeError: x", command="python app.py")
    assert last_failure()["command"] == "python app.py"


def test_claim_diff_withholds_secret_hunks_and_env_files(tmp_path, capsys):
    import subprocess

    from claimidx.cli import main

    tree = tmp_path / "repo"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tree / ".env").write_text("A=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=tree, check=True)
    (tree / ".env").write_text("A=1\nAWS=" + "AKIA" + "ABCDEFGHIJKLMNOP\n", encoding="utf-8")
    (tree / "a.py").write_text("x = 2\n", encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "--fmt", "json", "claim", "--err", "RuntimeError: diff probe", "--cwd", str(tree)]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert ".env" not in draft["fix_b"] and "AKIA" not in draft["fix_b"] and "+x = 2" in draft["fix_b"]
    (tree / "a.py").write_text("token = '" + "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345'\n", encoding="utf-8")
    assert main(["--db", db, "--fmt", "json", "claim", "--err", "RuntimeError: diff probe", "--cwd", str(tree)]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert "ghp_" not in draft["fix_b"] and "withheld" in draft["fix_b"]
