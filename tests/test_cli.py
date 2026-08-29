from pathlib import Path

from claimidx.cli import main
from claimidx.seed_data import materialize


def test_seed_ask_confirm_roundtrip(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    rc = main(["--db", db, "--fmt", "json", "ask", "--err", "TypeError: params is a Promise", "--eco", "npm", "--dep", "next@15.0.0"])
    out = capsys.readouterr().out
    assert rc == 0 and "spr_a11c000000000001" in out
    assert main(["--db", db, "--fmt", "id", "confirm", "spr_a11c000000000001"]) == 0


def test_publish_and_ls(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "--fmt", "id", "publish", "--err", "ModuleNotFoundError: No module named 'demo_mod'", "--eco", "py", "--fix-k", "pin", "--fix-b", "pip install demo-mod", "--eval", "true"])
    assert rc == 0
    cid = capsys.readouterr().out.strip()
    assert cid.startswith(("spr_", "cix_"))
    assert main(["--db", db, "ls"]) == 0
    assert cid in capsys.readouterr().out


def test_scan_does_not_require_identity(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:anon")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "scan", "--err", "TypeError: x", "--fix-k", "patch", "--fix-b", "await x", "--eval", "true"]) == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out


def test_claimidx_db_env(tmp_path: Path, capsys, monkeypatch):
    db = tmp_path / "from-env.sqlite"
    monkeypatch.setenv("CLAIMIDX_DB", str(db))
    assert main(["--fmt", "id", "publish", "--err", "ModuleNotFoundError: No module named 'envdb'", "--eco", "py", "--fix-k", "pin", "--fix-b", "pip install envdb", "--eval", "true"]) == 0
    capsys.readouterr()
    assert db.exists()
    assert main(["ls"]) == 0
    assert "module_not_found" in capsys.readouterr().out


def test_reject_cli(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "--fmt", "id", "publish", "--err", "ModuleNotFoundError: No module named 'rej'", "--eco", "py", "--fix-k", "pin", "--fix-b", "pip install rej", "--eval", "true"]) == 0
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "reject", cid]) == 0
    assert "rejected" in capsys.readouterr().out


def test_fail_missing(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "fail", "spr_aaaaaaaaaaaaaaaa"]) == 1


def test_force_publish_reuses_id(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "--fmt", "id", "publish", "--err", "ModuleNotFoundError: No module named 'force_mod'", "--eco", "py", "--fix-k", "pin", "--fix-b", "pip install force-mod==1", "--eval", "true"]) == 0
    first = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "id", "publish", "--force", "--err", "ModuleNotFoundError: No module named 'force_mod'", "--eco", "py", "--fix-k", "pin", "--fix-b", "pip install force-mod==2", "--eval", "true"]) == 0
    second = capsys.readouterr().out.strip()
    assert first == second
    assert main(["--db", db, "--fmt", "json", "show", first]) == 0
    assert "force-mod==2" in capsys.readouterr().out


def test_confirm_replay_json(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "--fmt", "id", "publish", "--err", "ModuleNotFoundError: No module named 'replay_mod'", "--eco", "py", "--fix-k", "pin", "--fix-b", "pip install replay-mod", "--eval", "true"]) == 0
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    out = capsys.readouterr().out
    assert '"held": true' in out
    assert '"builtin"' in out


def test_seed_materialize_count():
    claims = materialize()
    assert len(claims) >= 12
    assert len({c.id for c in claims}) == len(claims)
