"""0.7.30: `claimidx ask` accepts a positional error string as an alias of --err."""

from __future__ import annotations

from pathlib import Path

from claimidx.cli import build_parser, main


ERR = "TypeError: params is a Promise"


def test_positional_err_is_accepted(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "askpos", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    rc = main(["--db", db, "--fmt", "json", "ask", ERR, "--eco", "py"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert ERR.split()[-1] in captured.out or "pytest" in captured.out or captured.out
    # Same path as --err: a second call with --err is also ok (and not a conflict).
    rc2 = main(["--db", db, "--fmt", "json", "ask", "--err", ERR, "--eco", "py"])
    captured2 = capsys.readouterr()
    assert rc2 == 0, captured2.err
    import json

    a = json.loads(captured.out)
    b = json.loads(captured2.out)
    assert a["fp"] == b["fp"]
    assert a["hit"] is True and b["hit"] is True
    assert a["n"] == b["n"]


def test_positional_and_err_flag_conflict(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "askpos", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    rc = main(["--db", db, "ask", ERR, "--err", ERR])
    captured = capsys.readouterr()
    assert rc == 2
    assert "not both" in captured.err
    assert "positional" in captured.err
    assert "--err" in captured.err


def test_ask_without_err_or_positional_errors(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "askpos", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    rc = main(["--db", db, "ask"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "missing error text" in captured.err
    assert "--err" in captured.err


def test_query_alias_accepts_positional(tmp_path: Path, capsys):
    db = str(tmp_path / "ix.sqlite")
    assert main(["--db", db, "init", "--agent", "askpos", "--offline", "--no-hooks"]) == 0
    capsys.readouterr()
    rc = main(["--db", db, "--fmt", "json", "query", ERR, "--eco", "py"])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    import json

    out = json.loads(captured.out)
    assert out["fp"]
    assert out["hit"] is True


def test_ask_help_mentions_positional_alias():
    import io
    from contextlib import redirect_stdout, redirect_stderr

    buf = io.StringIO()
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            build_parser().parse_args(["ask", "--help"])
    except SystemExit:
        pass
    text = buf.getvalue()
    assert "--err" in text
    assert "positional" in text.lower() or "ERR" in text
    # parse a positional without going through main
    ns = build_parser().parse_args(["ask", "No module named pytest"])
    assert ns.err_pos == "No module named pytest"
    assert ns.err is None
    both = build_parser().parse_args(["ask", "one", "--err", "two"])
    assert both.err_pos == "one" and both.err == "two"
