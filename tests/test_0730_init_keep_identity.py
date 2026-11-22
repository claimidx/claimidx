"""0.7.30: bare `claimidx init` must keep an existing agent/owner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claimidx.cli import main
from claimidx import config


def _clear_identity_env(monkeypatch) -> None:
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cfg() -> dict:
    return json.loads(config.config_path().read_text(encoding="utf-8"))


def test_reinit_keeps_existing_agent_and_owner(tmp_path: Path, capsys, monkeypatch):
    _clear_identity_env(monkeypatch)
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "agent-e6f17f", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    identity = config.identity_path()
    before = _sha(identity)
    before_cfg = _cfg()
    assert before_cfg["agent"] == "agent-e6f17f"
    rc = main(["--db", db, "init", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    after = _cfg()
    assert after["agent"] == "agent-e6f17f"
    assert after["owner"] == "did:claimidx:agent-e6f17f"
    assert after["key"] == before_cfg["key"]
    assert _sha(identity) == before
    assert "auto-naming" not in captured.err
    assert "renamed" not in captured.err
    assert json.loads(captured.out)["agent"] == "agent-e6f17f"


def test_first_install_auto_names(tmp_path: Path, capsys, monkeypatch):
    _clear_identity_env(monkeypatch)
    assert not config.config_path().exists()
    assert not config.identity_path().exists()
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    data = _cfg()
    assert data["agent"].startswith("agent-") and len(data["agent"]) == len("agent-") + 6
    assert data["owner"] == f"did:claimidx:{data['agent']}"
    assert "no --agent given" in captured.err
    assert "auto-naming you " + data["agent"] in captured.err
    assert config.identity_path().exists()


def test_agent_flag_renames_and_prints_old_to_new(tmp_path: Path, capsys, monkeypatch):
    _clear_identity_env(monkeypatch)
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "agent-e6f17f", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    identity = config.identity_path()
    before = _sha(identity)
    rc = main(["--db", db, "init", "--agent", "ada", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "renamed agent-e6f17f → ada" in captured.err
    assert "did:claimidx:agent-e6f17f → did:claimidx:ada" in captured.err
    data = _cfg()
    assert data["agent"] == "ada"
    assert data["owner"] == "did:claimidx:ada"
    assert _sha(identity) == before
    out = json.loads(captured.out)
    assert out["agent"] == "ada"
    assert out["owner"] == "did:claimidx:ada"


def test_config_identity_mismatch_keeps_config_and_warns(tmp_path: Path, capsys, monkeypatch):
    _clear_identity_env(monkeypatch)
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "agent-e6f17f", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    identity = config.identity_path()
    payload = json.loads(identity.read_text(encoding="utf-8"))
    payload["agent"] = "agent-42a1b4"
    payload["owner"] = "did:claimidx:agent-42a1b4"
    identity.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    before = _sha(identity)
    rc = main(["--db", db, "init", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert "disagree" in captured.err
    assert "keeping config" in captured.err
    assert "identity.json is unchanged" in captured.err
    assert "auto-naming" not in captured.err
    data = _cfg()
    assert data["agent"] == "agent-e6f17f"
    assert data["owner"] == "did:claimidx:agent-e6f17f"
    assert _sha(identity) == before


def test_upgrade_from_0_7_28_config_format_keeps_name(tmp_path: Path, capsys, monkeypatch):
    """A config.json written the way 0.7.28 `config.save` wrote it must survive a 0.7.30 init."""
    _clear_identity_env(monkeypatch)
    from claimidx.identity import generate_identity

    key_path = config.identity_path()
    minted = generate_identity(key_path)
    # 0.7.28 init wrote these keys, indent=2, trailing newline, via write_private/save.
    legacy = {
        "owner": "did:claimidx:agent-e6f17f",
        "agent": "agent-e6f17f",
        "share": True,
        "key": minted["did"],
    }
    config.config_path().write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")
    before = _sha(key_path)
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    data = _cfg()
    assert data["agent"] == "agent-e6f17f"
    assert data["owner"] == "did:claimidx:agent-e6f17f"
    assert data["key"] == minted["did"]
    assert data["share"] is True
    assert _sha(key_path) == before
    assert "auto-naming" not in captured.err
    assert "renamed" not in captured.err


def test_fallback_to_identity_when_config_missing(tmp_path: Path, capsys, monkeypatch):
    _clear_identity_env(monkeypatch)
    from claimidx.identity import generate_identity
    from claimidx.perms import write_private

    minted = generate_identity(config.identity_path())
    payload = json.loads(config.identity_path().read_text(encoding="utf-8"))
    payload["agent"] = "agent-e6f17f"
    payload["owner"] = "did:claimidx:agent-e6f17f"
    write_private(config.identity_path(), json.dumps(payload, indent=2) + "\n")
    before = _sha(config.identity_path())
    assert not config.config_path().exists()
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    data = _cfg()
    assert data["agent"] == "agent-e6f17f"
    assert data["owner"] == "did:claimidx:agent-e6f17f"
    assert data["key"] == minted["did"]
    assert _sha(config.identity_path()) == before
    assert "auto-naming" not in captured.err


def test_identity_only_key_does_not_auto_name(tmp_path: Path, capsys, monkeypatch):
    _clear_identity_env(monkeypatch)
    from claimidx.identity import generate_identity

    minted = generate_identity(config.identity_path())
    before = _sha(config.identity_path())
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--offline", "--no-hooks"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    data = _cfg()
    assert data["owner"] == minted["did"]
    assert data["agent"] == ""
    assert data["key"] == minted["did"]
    assert _sha(config.identity_path()) == before
    assert "auto-naming" not in captured.err


def test_config_save_stays_atomic_and_identity_path_is_sibling(tmp_path: Path):
    path = config.config_path()
    assert config.identity_path() == path.parent / "identity.json"
    assert config.save.__doc__ and "atomic" in config.save.__doc__.lower()
    from claimidx.perms import write_private
    import inspect

    src = inspect.getsource(config.save)
    assert "write_private" in src
    # write_private already uses a temp file + os.replace, with a Windows fallback
    # when the target is open without delete-share.
    wp = inspect.getsource(write_private)
    assert "os.replace" in wp
    assert "Windows" in wp
