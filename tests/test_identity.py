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
    require_identity("did:claimidx:grok")


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
