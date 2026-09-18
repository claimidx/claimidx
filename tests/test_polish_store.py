"""Polish gate: connections close, private helpers stay inside store.py, every subcommand has help, one near_tie."""

import argparse
import sqlite3
from pathlib import Path

import pytest

import claimidx.store as store_mod
from claimidx import tokens
from claimidx.cli import build_parser
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.match import verdict_for
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.store import Store

SRC = Path(store_mod.__file__).parent


def _claim(err: str = "TypeError: params is a Promise") -> Claim:
    return Claim(
        fp=fingerprint(err=err, eco="npm", rt="node@20", dep=["next@15.0.0"]),
        cls="async_api",
        err=normalize_error(err),
        eco="npm",
        rt="node@20",
        dep=["next@15.0.0"],
        fix=Fix(k="patch", b="const { slug } = await params"),
        eval=EvalSpec(cmd="npx tsc --noEmit"),
        own="did:claimidx:test",
    )


# --- 1. _conn closes the connection it opened -------------------------------


class _Tracked(sqlite3.Connection):
    """sqlite3's C dealloc never calls this override; only an explicit close() flips the flag."""

    closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


@pytest.fixture
def opened(monkeypatch):
    real = sqlite3.connect
    seen: list[_Tracked] = []

    def connect(*args, **kwargs):
        kwargs["factory"] = _Tracked
        con = real(*args, **kwargs)
        seen.append(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", connect)
    return seen


def test_store_closes_every_connection_it_opens(tmp_path: Path, opened):
    store = Store(tmp_path / "ix.sqlite")
    c = store.put(_claim())
    assert store.get(c.id) is not None
    assert store.by_fp(c.fp) and store.all()
    assert opened, "the store opened no connection"
    assert all(con.closed for con in opened)


def test_store_closes_connection_on_error(tmp_path: Path, opened):
    store = Store(tmp_path / "ix.sqlite")
    with pytest.raises(sqlite3.OperationalError):
        with store._conn() as con:
            con.execute("SELECT 1 FROM no_such_table")
    assert all(con.closed for con in opened)


def test_store_early_return_inside_block_still_closes(tmp_path: Path, opened):
    store = Store(tmp_path / "ix.sqlite")
    assert store.delete("nope") is False  # returns from inside the with block
    assert all(con.closed for con in opened)


# --- 2. no private helpers used across modules --------------------------------


def test_only_store_opens_connections():
    offenders = sorted(p.name for p in SRC.glob("*.py") if p.name != "store.py" and "._conn(" in p.read_text(encoding="utf-8"))
    assert offenders == []


def test_drafts_module_has_no_ddl():
    assert "CREATE TABLE" not in (SRC / "drafts.py").read_text(encoding="utf-8")


def test_cli_does_not_reach_into_tokens_private_loader():
    assert "tokens._load" not in (SRC / "cli.py").read_text(encoding="utf-8")


def test_draft_accessors_roundtrip(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    payload = {"id": "draft_x", "err": "E", "n": 1}
    store.put_draft("draft_x", "fp1", payload, "2026-01-01T00:00:00+00:00")
    assert store.get_draft("draft_x") == payload
    assert store.get_draft("draft_missing") is None
    assert store.delete_draft("draft_x") is True
    assert store.get_draft("draft_x") is None
    assert store.delete_draft("draft_x") is False


def test_all_events_oldest_first_with_raw_detail(tmp_path: Path):
    store = Store(tmp_path / "ix.sqlite")
    store.log("ask", "did:claimidx:a", "", {"hit": False})
    store.log("publish", "did:claimidx:b", "spr_1")
    rows = store.all_events()
    assert [r["kind"] for r in rows] == ["ask", "publish"]
    assert rows[0]["actor"] == "did:claimidx:a" and rows[0]["detail"] == '{"hit": false}'
    assert rows[1]["claim_id"] == "spr_1" and rows[1]["detail"] is None


def test_tokens_count_public(tmp_path: Path):
    assert tokens.count() == 0
    tokens.mint("ci")
    tokens.mint("ci")  # same name replaces, not appends
    tokens.mint("ops")
    assert tokens.count() == 2


# --- 3. no __import__ in store.py --------------------------------------------


def test_store_imports_re_normally():
    assert "__import__(" not in (SRC / "store.py").read_text(encoding="utf-8")


# --- 4. every subparser has one-line help in one style -----------------------


def _walk(parser: argparse.ArgumentParser, prefix: str = ""):
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        helps = {ca.dest: ca.help for ca in action._choices_actions}
        parsers: dict[int, tuple[str, argparse.ArgumentParser]] = {}
        for name, child in action.choices.items():
            parsers.setdefault(id(child), (name, child))
        for name, child in parsers.values():
            yield prefix + name, helps.get(name)
            yield from _walk(child, prefix + name + " ")


def test_every_subcommand_has_one_line_help():
    rows = list(_walk(build_parser()))
    names = {n for n, _ in rows}
    assert {"ask", "confirm", "scan", "fail", "reject", "show", "ls", "fp", "stats", "seed", "export", "serve", "whoami", "team", "ingest"} <= names
    assert {"home-pull", "home-ask", "home-push", "home-propose", "token new", "token ls", "proof validate", "identity keygen"} <= names
    missing = [n for n, h in rows if not (h or "").strip()]
    assert missing == []
    for name, text in rows:
        assert text[0].islower(), f"{name}: help must start lowercase: {text!r}"
        assert not text.rstrip().endswith("."), f"{name}: help must not end with a period: {text!r}"
        assert "\n" not in text, f"{name}: help must be one line"


def test_ask_keeps_query_alias():
    ns = build_parser().parse_args(["query", "--err", "x"])
    assert ns.cmd == "query" and ns.err == "x"


# --- 5. one near_tie ---------------------------------------------------------


def test_cli_has_no_private_near_tie_copy():
    import claimidx.cli as cli

    assert not hasattr(cli, "_hook_near_tie")


def test_verdict_uses_hook_near_tie(monkeypatch):
    import claimidx.hook as hook

    monkeypatch.setattr(hook, "near_tie", lambda a, b: True)
    first, second = _claim(), _claim("TypeError: searchParams is a Promise")
    assert first.id != second.id
    query = {"err": first.err, "eco": "npm", "rt": "node@20", "dep": list(first.dep)}
    v = verdict_for(query, [(first, 0.9), (second, 0.5)])
    assert f"near-tie with {second.id}" in v["why"]
