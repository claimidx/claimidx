from pathlib import Path

from claimidx.cli import main


def test_init_requires_an_agent(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.delenv("CLAIMIDX_OWNER", raising=False)
    monkeypatch.delenv("CLAIMIDX_AGENT", raising=False)
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(tmp_path / "config.json"))
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "init", "--offline"])
    assert rc == 2
    assert "any-name" in capsys.readouterr().err


def test_init_offline_seeds_and_writes_config(tmp_path: Path, capsys, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(cfg))
    db = str(tmp_path / "ix.sqlite")
    rc = main(["--db", db, "init", "--agent", "harper", "--offline"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "did:claimidx:harper" in out
    assert cfg.exists()
    assert main(["--db", db, "ls"]) == 0
    listed = capsys.readouterr().out
    assert "spr_a11c000000000001" in listed
    hook = tmp_path / "claude" / "settings.json"
    assert hook.exists()
    import json
    data = json.loads(hook.read_text(encoding="utf-8"))
    blob = json.dumps(data)
    assert "PostToolUseFailure" in blob
    assert "claimidx hook" in blob
    assert main(["--db", db, "doctor"]) in (0, 2)
