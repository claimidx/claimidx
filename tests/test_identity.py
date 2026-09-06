import json
from pathlib import Path

import pytest

from claimidx.cli import main
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.policy import PolicyError, require_identity
from claimidx.store import Store
from claimidx.fingerprint import fingerprint, normalize_error


def test_require_identity_refuses_anon():
    with pytest.raises(PolicyError):
        require_identity("did:claimidx:anon")
    with pytest.raises(PolicyError):
        require_identity("")
    require_identity("did:claimidx:seed", src="seed")
    require_identity("did:claimidx:agent-a")


def test_store_refuses_anon_local_write(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    err = "TypeError: params is a Promise"
    with pytest.raises(PolicyError):
        store.put(
            Claim(
                fp=fingerprint(err=err, eco="npm"),
                cls="async_api",
                err=normalize_error(err),
                eco="npm",
                fix=Fix(k="patch", b="await params"),
                eval=EvalSpec(cmd="true"),
                own="did:claimidx:anon",
                src="local",
            )
        )


def test_cli_publish_refuses_anon(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:anon")
    db = str(tmp_path / "ix.sqlite")
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "id",
            "publish",
            "--err",
            "ModuleNotFoundError: No module named 'anon_mod'",
            "--eco",
            "py",
            "--fix-k",
            "pin",
            "--fix-b",
            "pip install anon-mod",
            "--eval",
            "true",
            "--own",
            "did:claimidx:anon",
        ]
    )
    assert rc != 0
    err = capsys.readouterr().err
    assert "anonymous" in err.lower() or "refused" in err.lower() or "error" in err.lower()


def test_first_write_provisions_identity_when_nothing_is_configured(tmp_path: Path, capsys, monkeypatch):
    """No --own, no env, no config: the first write creates an owner DID and a key instead of refusing."""
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "json",
            "publish",
            "--err",
            "RuntimeError: auto identity probe",
            "--eco",
            "py",
            "--fix-k",
            "constraint",
            "--fix-b",
            "ok",
            "--eval",
            "true",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    out = json.loads(captured.out)
    assert out["own"].startswith("did:claimidx:") and out["own"] != "did:claimidx:anon"
    assert "identity created" in captured.err
    from claimidx import config

    data = json.loads(config.config_path().read_text(encoding="utf-8"))
    assert data["owner"] == out["own"]
    assert data["key"].startswith("did:key:")
    assert (config.config_path().parent / "identity.json").exists()
    # Second write is silent and reuses it.
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "json",
            "publish",
            "--err",
            "RuntimeError: auto identity probe 2",
            "--eco",
            "py",
            "--fix-k",
            "constraint",
            "--fix-b",
            "ok",
            "--eval",
            "true",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0 and "identity created" not in captured.err
    assert json.loads(captured.out)["own"] == out["own"]


def test_auto_identity_can_be_disabled(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    monkeypatch.setenv("CLAIMIDX_AUTO_IDENTITY", "0")
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "publish", "--err", "RuntimeError: no auto", "--eco", "py", "--fix-k", "constraint", "--fix-b", "ok", "--eval", "true"])
    assert rc != 0
    assert "refused" in capsys.readouterr().err.lower()


def test_init_without_agent_picks_a_name_and_a_key(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "init", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    from claimidx import config

    data = json.loads(config.config_path().read_text(encoding="utf-8"))
    assert data["owner"].startswith("did:claimidx:") and data["agent"]
    assert data["key"].startswith("did:key:")
    assert main(["--db", db, "whoami"]) == 0
    who = capsys.readouterr().out
    assert data["key"] in who


def test_observations_are_signed_silently_with_the_local_key(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "RuntimeError: signed obs probe",
                "--eco",
                "py",
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "confirm", cid]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "explain", cid]) == 0
    graph = json.loads(capsys.readouterr().out)
    obs = graph["observations"][-1]
    from claimidx import config
    from claimidx.identity import verify_record

    key = json.loads(config.config_path().read_text(encoding="utf-8"))["key"]
    assert obs["key_id"] == key and obs["signature"]
    assert verify_record(obs)
    # Tamper: the signature no longer verifies.
    obs["held"] = not obs["held"]
    assert not verify_record(obs)
