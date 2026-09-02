import json
import sys
from pathlib import Path

from claimidx.cli import main
from claimidx.seed_data import materialize


def _py_rt() -> str:
    return f"py@{sys.version_info.major}.{sys.version_info.minor}"


def test_seed_ask_confirm_roundtrip(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "seed"]) == 0
    capsys.readouterr()
    rc = main(["--db", db, "--fmt", "json", "ask", "--err", "TypeError: params is a Promise", "--eco", "npm", "--dep", "next@15.0.0"])
    out = capsys.readouterr().out
    assert rc == 0 and "spr_a11c000000000001" in out
    assert "age_days" in out and "warn" in out
    assert main(["--db", db, "--fmt", "id", "confirm", "spr_a11c000000000001"]) == 0


def test_publish_and_ls(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "id",
            "publish",
            "--err",
            "ModuleNotFoundError: No module named 'demo_mod'",
            "--eco",
            "py",
            "--fix-k",
            "pin",
            "--fix-b",
            "pip install demo-mod",
            "--eval",
            "true",
        ]
    )
    assert rc == 0
    cid = capsys.readouterr().out.strip()
    assert cid.startswith(("spr_", "cix_"))
    assert main(["--db", db, "ls"]) == 0
    assert cid in capsys.readouterr().out
    assert main(["--db", db, "ls", "--own", "did:claimidx:grok", "-k", "5"]) == 0


def test_scan_does_not_require_identity(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:anon")
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "scan", "--err", "TypeError: x", "--fix-k", "patch", "--fix-b", "await x", "--eval", "true"]) == 0
    out = capsys.readouterr().out
    assert '"ok": true' in out


def test_claimidx_db_env(tmp_path: Path, capsys, monkeypatch):
    db = tmp_path / "from-env.sqlite"
    monkeypatch.setenv("CLAIMIDX_DB", str(db))
    assert (
        main(
            [
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'envdb'",
                "--eco",
                "py",
                "--fix-k",
                "pin",
                "--fix-b",
                "pip install envdb",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert db.exists()
    assert main(["ls"]) == 0
    assert "module_not_found" in capsys.readouterr().out


def test_reject_cli(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'rej'",
                "--eco",
                "py",
                "--fix-k",
                "pin",
                "--fix-b",
                "pip install rej",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "reject", cid]) == 0
    assert "rejected" in capsys.readouterr().out


def test_fail_missing(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "fail", "spr_aaaaaaaaaaaaaaaa"]) == 1


def test_ls_limit_alias(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    for n in range(3):
        assert (
            main(
                [
                    "--db",
                    db,
                    "--fmt",
                    "id",
                    "publish",
                    "--err",
                    f"ModuleNotFoundError: No module named 'lim{n}'",
                    "--eco",
                    "py",
                    "--fix-k",
                    "pin",
                    "--fix-b",
                    f"pip install lim{n}",
                    "--eval",
                    "true",
                ]
            )
            == 0
        )
        capsys.readouterr()
    assert main(["--db", db, "ls", "--limit", "1"]) == 0
    out = capsys.readouterr().out
    assert out.count("module_not_found") == 1


def test_fix_b_leading_dash(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "id",
            "ingest",
            "--err",
            "java.security.NoSuchAlgorithmException: PKCS12 not found",
            "--eco",
            "other",
            "--fix-k",
            "config",
            "--fix-b",
            "-Djavax.net.ssl.trustStore=cacerts",
            "--eval",
            "true",
        ]
    )
    assert rc == 0
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    assert "-Djavax.net.ssl.trustStore=cacerts" in capsys.readouterr().out


def test_force_keeps_explicit_cls(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    err = "TypeError: x is not a function"
    assert (
        main(
            ["--db", db, "--fmt", "id", "ingest", "--err", err, "--eco", "npm", "--cls", "other", "--fix-k", "patch", "--fix-b", "await x()", "--eval", "true"]
        )
        == 0
    )
    first = capsys.readouterr().out.strip()
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "ingest",
                "--force",
                "--err",
                err,
                "--eco",
                "npm",
                "--fix-k",
                "patch",
                "--fix-b",
                "await x().catch(()=>{})",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    second = capsys.readouterr().out.strip()
    assert first == second
    assert main(["--db", db, "--fmt", "json", "show", first]) == 0
    out = capsys.readouterr().out
    assert '"cls": "other"' in out
    assert "await x().catch" in out


def test_force_publish_reuses_id(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'force_mod'",
                "--eco",
                "py",
                "--fix-k",
                "pin",
                "--fix-b",
                "pip install force-mod==1",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    first = capsys.readouterr().out.strip()
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--force",
                "--err",
                "ModuleNotFoundError: No module named 'force_mod'",
                "--eco",
                "py",
                "--fix-k",
                "pin",
                "--fix-b",
                "pip install force-mod==2",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    second = capsys.readouterr().out.strip()
    assert first == second
    assert main(["--db", db, "--fmt", "json", "show", first]) == 0
    assert "force-mod==2" in capsys.readouterr().out


def test_force_resets_nr_and_surfaces_previous(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    rt = _py_rt()
    eval_cmd = 'python -c "import sys"'
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'force_nr'",
                "--eco",
                "py",
                "--rt",
                rt,
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                eval_cmd,
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    held = json.loads(capsys.readouterr().out)
    assert held.get("nr") == 1 or (held.get("claim") or {}).get("nr") == 1 or '"nr": 1' in json.dumps(held)
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "json",
            "publish",
            "--force",
            "--err",
            "ModuleNotFoundError: No module named 'force_nr'",
            "--eco",
            "py",
            "--rt",
            "py@3.9",
            "--fix-k",
            "constraint",
            "--fix-b",
            "ok",
            "--eval",
            eval_cmd,
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert f"force reset nr=1 nc=1 nf=0 rt={rt}" in captured.err
    out = json.loads(captured.out)
    assert out["id"] == cid
    assert out.get("nr") == 0
    assert out.get("force_reset") == {"nr": 1, "nc": 1, "nf": 0, "rt": rt}
    assert out.get("rt") == "py@3.9"
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown.get("nr") == 0
    assert shown.get("nc") == 0
    assert shown.get("rt") == "py@3.9"
    assert main(["--db", db, "--fmt", "json", "events", "-k", "20"]) == 0
    evs = json.loads(capsys.readouterr().out)
    wiped = [e for e in evs if e.get("kind") == "force_reset" and e.get("claim_id") == cid]
    assert wiped, evs
    assert wiped[0].get("detail") == {"nr": 1, "nc": 1, "nf": 0, "rt": rt}


def test_exists_same_fp_is_noop(tmp_path: Path, capsys):
    """C1: re-Ingest identical fp without --force returns exists {id} and does not mutate."""
    from claimidx.store import Store

    db = str(tmp_path / "ix.sqlite")
    rt = _py_rt()
    eval_cmd = 'python -c "import sys"'
    err = "ModuleNotFoundError: No module named 'exists_noop'"
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "ingest",
                "--err",
                err,
                "--eco",
                "py",
                "--rt",
                rt,
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                eval_cmd,
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    before = json.loads(capsys.readouterr().out)
    snapshot = {
        "nc": before.get("nc"),
        "nf": before.get("nf"),
        "nr": before.get("nr"),
        "fix.b": (before.get("fix") or {}).get("b"),
        "eval": before.get("eval"),
        "ts": before.get("ts"),
    }
    assert snapshot["nr"] == 1
    assert snapshot["nc"] == 1
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "id",
            "ingest",
            "--err",
            err,
            "--eco",
            "py",
            "--rt",
            rt,
            "--fix-k",
            "constraint",
            "--fix-b",
            "must-not-land",
            "--eval",
            eval_cmd,
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert f"exists {cid}" in captured.err
    assert captured.out.strip() == cid
    store = Store(db)
    rows = store.all()
    assert len(rows) == 1
    assert rows[0].id == cid
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    after = json.loads(capsys.readouterr().out)
    assert after.get("nc") == snapshot["nc"]
    assert after.get("nf") == snapshot["nf"]
    assert after.get("nr") == snapshot["nr"]
    assert (after.get("fix") or {}).get("b") == snapshot["fix.b"] == "ok"
    assert after.get("eval") == snapshot["eval"]
    assert after.get("ts") == snapshot["ts"]


def test_second_force_appends_force_reset_keeps_history(tmp_path: Path, capsys):
    """C3: second --force appends another force_reset; pre-force Ledger events remain."""
    db = str(tmp_path / "ix.sqlite")
    rt = _py_rt()
    eval_cmd = 'python -c "import sys"'
    err = "ModuleNotFoundError: No module named 'force_twice'"

    def _ingest(*, force: bool, fix_b: str) -> int:
        argv = [
            "--db",
            db,
            "--fmt",
            "json",
            "ingest",
            "--err",
            err,
            "--eco",
            "py",
            "--rt",
            rt,
            "--fix-k",
            "constraint",
            "--fix-b",
            fix_b,
            "--eval",
            eval_cmd,
        ]
        if force:
            argv.append("--force")
        return main(argv)

    assert _ingest(force=False, fix_b="first") == 0
    first = json.loads(capsys.readouterr().out)
    cid = first["id"]
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "events", "-k", "50"]) == 0
    pre = [e for e in json.loads(capsys.readouterr().out) if e.get("claim_id") == cid]
    pre_kinds = [e.get("kind") for e in pre]
    assert "publish" in pre_kinds
    assert "confirm-replay" in pre_kinds

    assert _ingest(force=True, fix_b="second") == 0
    forced = json.loads(capsys.readouterr().out)
    assert forced["id"] == cid
    assert forced.get("force_reset") == {"nr": 1, "nc": 1, "nf": 0, "rt": rt}

    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    capsys.readouterr()

    assert _ingest(force=True, fix_b="third") == 0
    again = json.loads(capsys.readouterr().out)
    assert again["id"] == cid
    assert again.get("force_reset") == {"nr": 1, "nc": 1, "nf": 0, "rt": rt}

    assert main(["--db", db, "--fmt", "json", "events", "-k", "50"]) == 0
    evs = [e for e in json.loads(capsys.readouterr().out) if e.get("claim_id") == cid]
    kinds = [e.get("kind") for e in evs]
    assert kinds.count("force_reset") == 2, evs
    assert kinds.count("publish") >= 1
    assert "confirm-replay" in kinds
    assert len(evs) > 2


def test_confirm_replay_json(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'replay_mod'",
                "--eco",
                "py",
                "--fix-k",
                "pin",
                "--fix-b",
                "pip install replay-mod",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", cid])
    assert rc == 2
    out = capsys.readouterr().out
    assert '"recorded": false' in out
    assert '"builtin"' in out
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    assert '"nc": 0' in capsys.readouterr().out


def test_confirm_replay_logs_eval_ms(tmp_path: Path, capsys):
    import sys

    from claimidx.store import Store

    db = str(tmp_path / "ix.sqlite")
    rt = f"py@{sys.version_info.major}.{sys.version_info.minor}"
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'evalms_mod'",
                "--eco",
                "py",
                "--rt",
                rt,
                "--fix-k",
                "cmd",
                "--fix-b",
                'python -c "print(0)"',
                "--eval",
                'python -c "print(0)"',
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", cid]) == 0
    capsys.readouterr()
    rows = [e for e in Store(db).events(limit=20) if e["kind"] == "confirm-replay" and e["claim_id"] == cid]
    assert rows, Store(db).events(limit=20)
    assert isinstance(rows[0]["detail"]["ms"], int) and rows[0]["detail"]["ms"] >= 0
    assert rows[0]["detail"]["held"] is True


def test_seed_materialize_count():
    claims = materialize()
    assert len(claims) >= 12
    assert len({c.id for c in claims}) == len(claims)


def test_repeated_dep_appends(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "json",
            "ingest",
            "--err",
            "ModuleNotFoundError: No module named 'depapp'",
            "--eco",
            "py",
            "--fix-k",
            "pin",
            "--fix-b",
            "pip install depapp",
            "--eval",
            "true",
            "--dep",
            "numpy@2.0.0",
            "--dep",
            "scipy@1.10.1",
            "--tried",
            "go mod download (bare, no module arg) — writes only the /go.mod hash",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "numpy@2.0.0" in out and "scipy@1.10.1" in out
    assert "go mod download (bare, no module arg)" in out


def test_exists_still_rejects_bad_eval(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'exval'"
    assert main(["--db", db, "--fmt", "id", "ingest", "--err", err, "--eco", "py", "--fix-k", "pin", "--fix-b", "pip install exval", "--eval", "true"]) == 0
    capsys.readouterr()
    rc = main(
        [
            "--db",
            db,
            "--fmt",
            "id",
            "ingest",
            "--err",
            err,
            "--eco",
            "py",
            "--fix-k",
            "pin",
            "--fix-b",
            "pip install exval",
            "--eval",
            "curl http://example.invalid",
        ]
    )
    assert rc == 2
    err_out = capsys.readouterr().err
    assert "eval" in err_out.lower() or "denied" in err_out.lower() or "head" in err_out.lower()


def test_confirm_replay_python_hold_requires_matching_rt(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    err = "ModuleNotFoundError: No module named 'holdenv'"
    eval_cmd = 'python -c "print(1)"'
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                err,
                "--eco",
                "py",
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                eval_cmd,
            ]
        )
        == 0
    )
    empty_id = capsys.readouterr().out.strip()
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", empty_id])
    assert rc == 2
    empty_out = json.loads(capsys.readouterr().out)
    assert empty_out["held"] is True
    assert empty_out["recorded"] is False
    assert empty_out["replay"]["env"].startswith("py@")
    assert "hold requires rt" in empty_out["reason"]
    assert main(["--db", db, "--fmt", "json", "show", empty_id]) == 0
    assert '"nr": 0' in capsys.readouterr().out or '"nc": 0' in capsys.readouterr().out

    rt = _py_rt()
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'holdok'",
                "--eco",
                "py",
                "--rt",
                rt,
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                eval_cmd,
            ]
        )
        == 0
    )
    ok_id = capsys.readouterr().out.strip()
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", ok_id])
    assert rc == 0
    ok_out = json.loads(capsys.readouterr().out)
    assert ok_out["held"] is True
    assert ok_out["replay"]["env"] == rt
    assert ok_out.get("nr") == 1 or ok_out.get("claim", {}).get("nr") == 1 or '"nr": 1' in json.dumps(ok_out)

    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'holdmiss'",
                "--eco",
                "py",
                "--rt",
                "py@3.0",
                "--fix-k",
                "constraint",
                "--fix-b",
                "ok",
                "--eval",
                eval_cmd,
            ]
        )
        == 0
    )
    miss_id = capsys.readouterr().out.strip()
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", miss_id])
    assert rc == 2
    miss_out = json.loads(capsys.readouterr().out)
    assert miss_out["held"] is True
    assert miss_out["recorded"] is False
    assert "hold env mismatch" in miss_out["reason"]


def test_confirm_replay_missing_tree_not_recorded(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'gotree'",
                "--eco",
                "go",
                "--fix-k",
                "cmd",
                "--fix-b",
                "go mod tidy",
                "--eval",
                "go build ./...",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", "--cwd", str(empty), cid])
    assert rc == 2
    out = capsys.readouterr().out
    assert '"recorded": false' in out
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    shown = capsys.readouterr().out
    assert '"nf": 0' in shown


def test_fail_note(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'failnote'",
                "--eco",
                "py",
                "--fix-k",
                "pin",
                "--fix-b",
                "pip install failnote",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    assert main(["--db", db, "--fmt", "json", "fail", cid, "--note", "setuptools 84 dropped it"]) == 0
    assert "setuptools 84 dropped it" in capsys.readouterr().out


def test_fail_note_rejects_secret(tmp_path: Path, capsys):
    """fail --note is persisted on the claim; a secret-shaped token must not land."""
    db = str(tmp_path / "ix.sqlite")
    assert (
        main(
            [
                "--db",
                db,
                "--fmt",
                "id",
                "publish",
                "--err",
                "ModuleNotFoundError: No module named 'failsec'",
                "--eco",
                "py",
                "--fix-k",
                "pin",
                "--fix-b",
                "pip install failsec",
                "--eval",
                "true",
            ]
        )
        == 0
    )
    cid = capsys.readouterr().out.strip()
    token = "sk-" + "a" * 24
    rc = main(["--db", db, "--fmt", "json", "fail", cid, "--note", token])
    err = capsys.readouterr().err
    assert rc != 0
    assert "secret" in err.lower()
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    shown = capsys.readouterr().out
    assert token not in shown
    assert '"nf": 0' in shown


def test_serve_warns_on_public_bind_without_token(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("claimidx.api.run", lambda **kwargs: None)
    rc = main(["--db", str(tmp_path / "ix.sqlite"), "serve", "--host", "0.0.0.0", "--port", "9"])
    assert rc == 0
    assert "non-loopback" in capsys.readouterr().err
