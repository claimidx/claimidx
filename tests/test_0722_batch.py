"""0.7.22 batch: private file modes from creation, scratch share text, no "held" on unrecorded replays, eval_passed false on a miss."""

from __future__ import annotations

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from claimidx import perms, sandbox
from claimidx.cli import main

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")


def _mode(p: Path) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture
def umask0():
    """Worst case: with umask 0 a plain open() creates 0666 and mkdir 0777."""
    old = os.umask(0)
    try:
        yield
    finally:
        os.umask(old)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for k in ("CLAIMIDX_OWNER", "CLAIMIDX_AGENT", "CLAIMIDX_CONFIG", "CLAIMIDX_DB", "CLAIMIDX_OUTBOX", "CLAIMIDX_SCRATCH_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    return home


@pytest.fixture
def no_bwrap(monkeypatch):
    real = shutil.which
    monkeypatch.setattr(sandbox.shutil, "which", lambda name, *a, **k: None if name == "bwrap" else real(name, *a, **k))


def _publish(db: str, capsys, eval_cmd: str) -> str:
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
    argv = ["--db", db, "--fmt", "id", "publish", "--err", "RuntimeError: 0722 probe", "--eco", "py", "--rt", rt]
    argv += ["--fix-k", "patch", "--fix-b", "x", "--eval", eval_cmd, "--local"]
    assert main(argv) == 0
    return capsys.readouterr().out.strip()


# --- 1: file permissions --------------------------------------------------------------------


@posix_only
def test_fresh_install_home_is_0700_and_files_0600(fake_home: Path, capsys, umask0):
    from claimidx.env import remember_failure

    tree = fake_home.parent / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    root = fake_home / ".claimidx"
    db = str(root / "index.sqlite")  # store.DEFAULT_DB is fixed at import; name the fresh home's path
    assert main(["--db", db, "--fmt", "json", "claim", "--yes", "--no-diff", "--no-clean-room", "--fix", "pip install json"]) == 0
    capsys.readouterr()
    assert _mode(root) == 0o700, oct(_mode(root))
    names = {p.name for p in root.iterdir()}
    assert {"config.json", "index.sqlite"} <= names, names
    for p in root.rglob("*"):
        want = 0o700 if p.is_dir() else 0o600
        assert _mode(p) == want, (p.name, oct(_mode(p)))


@posix_only
def test_existing_loose_home_is_tightened_on_first_run(fake_home: Path, capsys):
    root = fake_home / ".claimidx"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    for name in ("config.json", "identity.json", "index.sqlite", "index.sqlite-wal", "index.sqlite-shm", "outbox.jsonl"):
        (root / name).write_text("{}" if name.endswith(".json") else "", encoding="utf-8")
        os.chmod(root / name, 0o644)
    (root / "sub").mkdir()
    os.chmod(root / "sub", 0o755)
    (root / "sub" / "f").write_text("x", encoding="utf-8")
    os.chmod(root / "sub" / "f", 0o666)
    main(["--db", str(fake_home.parent / "other.sqlite"), "--fmt", "json", "stats"])
    capsys.readouterr()
    assert _mode(root) == 0o700
    assert _mode(root / "sub") == 0o700
    for p in root.rglob("*"):
        if p.is_file():
            assert _mode(p) == 0o600, (p.name, oct(_mode(p)))


@posix_only
def test_secure_home_skips_symlinks(fake_home: Path, tmp_path: Path):
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    os.chmod(outside, 0o644)
    root = fake_home / ".claimidx"
    root.mkdir()
    (root / "link").symlink_to(outside)
    perms.secure_home(force=True)
    assert _mode(outside) == 0o644  # never chmod through a link to a file claimidx does not own


@posix_only
def test_write_private_has_no_world_readable_window(tmp_path: Path, monkeypatch, umask0):
    seen: list[int] = []
    real = os.replace

    def spy(src, dst):
        seen.append(_mode(Path(src)))  # the temp file's mode before it becomes the real path
        return real(src, dst)

    monkeypatch.setattr(perms.os, "replace", spy)
    target = tmp_path / "new" / "config.json"
    perms.write_private(target, '{"a": 1}')
    assert seen == [0o600]
    assert _mode(target) == 0o600
    assert _mode(target.parent) == 0o700
    os.chmod(target, 0o644)
    perms.write_private(target, '{"a": 2}')  # rewrite of a loose file comes out 0600 too
    assert _mode(target) == 0o600 and json.loads(target.read_text())["a"] == 2


@posix_only
def test_config_identity_outbox_store_are_0600_under_umask0(tmp_path: Path, monkeypatch, umask0):
    from claimidx import config, home
    from claimidx.store import Store

    cfg = tmp_path / "c" / "config.json"
    monkeypatch.setenv("CLAIMIDX_CONFIG", str(cfg))
    config.save({"owner": "did:claimidx:t0722"})
    assert _mode(cfg) == 0o600

    out = tmp_path / "o" / "outbox.jsonl"
    perms.append_private(out, "{}\n")
    perms.append_private(out, "{}\n")
    assert _mode(out) == 0o600 and out.read_text().count("\n") == 2
    assert home is not None

    db = tmp_path / "s" / "index.sqlite"
    store = Store(db)
    store.list() if hasattr(store, "list") else None
    for p in db.parent.iterdir():
        assert _mode(p) == 0o600, (p.name, oct(_mode(p)))
    assert _mode(db) == 0o600


@posix_only
def test_scratch_dir_0700_and_every_file_0600(fake_home: Path, tmp_path: Path, capsys, monkeypatch, umask0):
    import tempfile

    from claimidx import cli
    from claimidx.env import remember_failure

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    snaps: list[dict] = []
    real = shutil.rmtree

    def spy(path, *a, **kw):
        p = Path(path)
        snaps.append({"dir": _mode(p), "files": {f.name: _mode(f) for f in p.rglob("*")}})
        return real(path, *a, **kw)

    monkeypatch.setattr(cli.shutil, "rmtree", spy)
    tree = tmp_path / "tree"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    assert main(["--scratch", "--fmt", "json", "claim", "--yes", "--no-diff", "--no-clean-room", "--fix", "pip install json"]) == 0
    capsys.readouterr()
    assert len(snaps) == 1
    snap = snaps[0]
    assert snap["dir"] == 0o700
    assert "index.sqlite" in snap["files"], snap
    for name, mode in snap["files"].items():
        assert mode in (0o600, 0o700), (name, oct(mode))


def test_perms_are_a_noop_off_posix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(perms, "POSIX", False)
    calls: list = []
    monkeypatch.setattr(perms.os, "chmod", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(perms.os, "fchmod", lambda *a, **k: calls.append(a), raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    (tmp_path / ".claimidx").mkdir()
    perms.secure_home(force=True)
    target = perms.write_private(tmp_path / "x" / "config.json", "{}")
    perms.append_private(tmp_path / "x" / "o.jsonl", "{}\n")
    assert target.read_text() == "{}"
    assert calls == []


# --- 2: --scratch says the claim is throwaway, never names a share step -------------------------


def test_scratch_claim_text_says_throwaway_not_share(fake_home: Path, tmp_path: Path, capsys, monkeypatch):
    from claimidx.env import remember_failure
    from claimidx.home import SCRATCH_NO_SHARE

    for n, extra in enumerate(([], ["--share-yes"], ["--local"])):
        tree = tmp_path / f"tree{n}"
        tree.mkdir()
        remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
        assert main(["--scratch", "claim", "--yes", *extra, "--no-diff", "--no-clean-room", "--fix", "pip install json"]) == 0
        err = capsys.readouterr().err
        assert SCRATCH_NO_SHARE in err, err
        assert "claimidx share" not in err, err
        assert "CLAIMIDX_COMMONS=0" not in err and "commons: false" not in err, err
        assert "publish_no_share" not in err, err

    tree = tmp_path / "treej"
    tree.mkdir()
    remember_failure("ModuleNotFoundError: No module named 'json'", cwd=str(tree), eco="py")
    argv = ["--scratch", "--fmt", "json", "claim", "--yes", "--share-yes", "--no-diff", "--no-clean-room", "--fix", "pip install json"]
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    blob = json.dumps(out)
    assert "claimidx share" not in blob and "CLAIMIDX_COMMONS=0" not in blob, blob
    assert out["commons"] == {"status": "scratch", "reason": SCRATCH_NO_SHARE}
    assert out["path_b"]["scratch"] is True and out["path_b"]["countable"] is False
    assert out["path_b"]["why"] == SCRATCH_NO_SHARE and out["path_b"]["next"] == ""


def test_non_scratch_disabled_commons_text_is_unchanged(monkeypatch):
    from claimidx import home

    monkeypatch.delenv("CLAIMIDX_SCRATCH", raising=False)
    assert home.commons_disabled_note() == {"status": "disabled", "reason": home.COMMONS_DISABLED_REASON}
    assert "claimidx share" in home.local_status("cix_x")["hint"]
    monkeypatch.setenv("CLAIMIDX_SCRATCH", "1")
    assert home.commons_disabled_note()["status"] == "scratch"
    assert "claimidx share" not in home.local_status("cix_x")["hint"]


# --- 3: no "held" wording on a replay that is not a recorded hold ------------------------------


def _held_words(obj, path="") -> list[str]:
    """Every replay-shaped dict that says held/held_unrecorded while held is false, and every held* recovery."""
    bad: list[str] = []
    if isinstance(obj, dict):
        if obj.get("held") is False and "held" in str(obj.get("reason") or "").split(":")[0]:
            bad.append(f"{path}.reason={obj.get('reason')}")
        if "held" in str(obj.get("recovery") or ""):
            bad.append(f"{path}.recovery={obj.get('recovery')}")
        for k, v in obj.items():
            bad += _held_words(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad += _held_words(v, f"{path}[{i}]")
    return bad


def test_confirm_lights_never_say_held_unrecorded():
    from claimidx.match import confirm_lights

    assert confirm_lights(held=False, recorded=False, eval_passed=True, reason="proof-artifact-drift")["recovery"] == "unrecorded"
    assert confirm_lights(held=True, recorded=False)["recovery"] == "unrecorded"
    assert confirm_lights(held=True, recorded=True)["recovery"] == "reproduced"
    assert confirm_lights(held=False, recorded=False, reason="eval-miss")["recovery"] == "miss"
    lit = confirm_lights(held=False, recorded=False, eval_passed=True, reason="gate said no")
    assert lit["integrity"] == "ok"  # the eval ran and passed: integrity was checked


def test_refused_replay_info_drops_held_everywhere():
    info = {"held": True, "reason": "held", "rerun": {"held": True, "reason": "held"}, "rc": 0}
    out = sandbox.refused_replay_info(info)
    assert out["held"] is False and out["eval_passed"] is True and out["recorded"] is False
    assert out["reason"] == sandbox.NOT_RECORDED_REASON
    assert out["rerun"]["held"] is False and out["rerun"]["reason"] == sandbox.NOT_RECORDED_REASON
    assert _held_words(out) == []
    pin = sandbox.refused_replay_info({"held": True, "reason": "held-pin"})
    assert pin["reason"] == sandbox.NOT_RECORDED_REASON


def _refuse(monkeypatch):
    from claimidx import gate

    monkeypatch.setattr(gate, "graduation_gate", lambda *a, **k: gate.GateDecision(False, "test-refusal: not recorded"))


def test_cli_confirm_gate_refusal_has_no_held_wording(tmp_path: Path, capsys, monkeypatch, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, 'python -c "pass"')
    _refuse(monkeypatch)
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", cid, "--net"])
    out = json.loads(capsys.readouterr().out)
    assert rc != 0, out
    assert out["held"] is False and out["eval_passed"] is True and out["recorded"] is False, out
    assert out["replay"]["held"] is False and out["replay"]["reason"] == sandbox.NOT_RECORDED_REASON, out
    assert out["lights"]["recovery"] == "unrecorded", out
    assert _held_words(out) == [], _held_words(out)


def test_mcp_confirm_gate_refusal_has_no_held_wording(tmp_path: Path, monkeypatch, no_bwrap):
    from claimidx.mcp_server import _call
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:t0722-mcp")
    store = Store(tmp_path / "ix.sqlite")
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
    pub = _call(
        "claimidx_publish",
        {"err": "RuntimeError: 0722 mcp probe", "eco": "py", "rt": rt, "fix_k": "patch", "fix_b": "x", "eval": 'python -c "pass"', "local": True},
        store,
    )
    _refuse(monkeypatch)
    out = _call("claimidx_confirm", {"id": pub["id"], "replay": True, "net": True}, store)
    assert out.get("recorded") is False, out
    assert out["held"] is False and out["eval_passed"] is True, out
    assert out["lights"]["recovery"] == "unrecorded", out
    assert _held_words(out) == [], _held_words(out)


def test_claim_gate_refusal_reason_is_not_held(tmp_path: Path, capsys, monkeypatch, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    _refuse(monkeypatch)
    argv = ["--db", db, "--fmt", "json", "claim", "--err", "ModuleNotFoundError: No module named 'json'", "--cwd", str(tree)]
    argv += ["--eval", 'python -I -c "import json"', "--no-diff", "--no-clean-room", "--fix", "pip install json", "--yes"]
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    rep = out["replay"]
    assert rep["held"] is False and rep["recorded"] is False and rep["eval_passed"] is True, rep
    assert _held_words(out) == [], _held_words(out)


# --- 4: an eval miss says eval_passed false ----------------------------------------------------

_MISS = 'python -c "import sys; sys.exit(3)"'


def test_cli_confirm_eval_miss_sets_eval_passed_false(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    cid = _publish(db, capsys, _MISS)
    for net in ([], ["--net"]):
        main(["--db", db, "--fmt", "json", "confirm", "--replay", cid, *net])
        out = json.loads(capsys.readouterr().out)
        assert out["held"] is False and out["eval_passed"] is False, out
        assert out["replay"]["eval_passed"] is False, out
        assert out["lights"]["recovery"] in ("miss", "contested"), out


def test_mcp_confirm_eval_miss_sets_eval_passed_false(tmp_path: Path, monkeypatch, no_bwrap):
    from claimidx.mcp_server import _call
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:t0722-mcp")
    store = Store(tmp_path / "ix.sqlite")
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
    pub = _call(
        "claimidx_publish",
        {"err": "RuntimeError: 0722 mcp miss", "eco": "py", "rt": rt, "fix_k": "patch", "fix_b": "x", "eval": _MISS, "local": True},
        store,
    )
    for net in ({}, {"net": True}):
        out = _call("claimidx_confirm", {"id": pub["id"], "replay": True, **net}, store)
        assert out["held"] is False and out["eval_passed"] is False, out
        assert out["replay"]["eval_passed"] is False, out


def test_claim_eval_miss_sets_eval_passed_false(tmp_path: Path, capsys, no_bwrap):
    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    tree.mkdir()
    argv = ["--db", db, "--fmt", "json", "claim", "--err", "ModuleNotFoundError: No module named 'nope0722'", "--cwd", str(tree)]
    argv += ["--eval", _MISS, "--no-diff", "--no-clean-room", "--fix", "pip install nope0722", "--yes"]
    main(argv)
    out = json.loads(capsys.readouterr().out)
    rep = out["replay"]
    assert rep["held"] is False and rep["recorded"] is False, rep
    assert rep["eval_passed"] is False, rep
    assert rep["replay"]["eval_passed"] is False, rep


def test_eval_miss_info_is_explicit():
    assert sandbox.eval_miss_info({"held": False, "reason": "eval-miss"})["eval_passed"] is False
    assert sandbox.not_recorded_info({"held": False}, False)["eval_passed"] is False
    assert sandbox.not_recorded_info({"held": True, "reason": "held"}, True)["eval_passed"] is True
