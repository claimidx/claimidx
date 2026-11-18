"""0.7.26: credential URLs and password/token values are redacted before fingerprinting and storing."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PW = "s3cr" + "etpw"  # never a real secret; split so the sanitize gate does not see a literal

BANNER = f"[Redis Connected] Successfully connected to redis://user:{PW}@db.internal:6379"
DRIVER = "ReplyError: WRONGPASS invalid username-password pair or user is disabled."
CRED_ERR = f"[Redis Error] Connection: redis://user:{PW}@db.internal:6379"


@pytest.mark.parametrize(
    "url",
    [
        f"redis://user:{PW}@h:6379",
        f"rediss://:{PW}@h",
        f"postgres://u:{PW}@h/db",
        f"postgresql://u:{PW}@h/db",
        f"mysql://u:{PW}@h/db",
        f"mongodb+srv://u:{PW}@cluster.example/x",
        f"amqp://u:{PW}@mq/vhost",
        f"https://u:{PW}@github.com/org/repo.git",
        f"http://:{PW}@localhost:8080",
        f"claimidx://u:{PW}@home.example",
    ],
)
def test_credential_urls_are_redacted_and_rejected(url):
    from claimidx.security import SecretError, redact, redact_credentials, reject_secrets

    for fn in (redact_credentials, redact):
        out = fn(f"Error: cannot connect {url}")
        assert PW not in out and "<CRED>@" in out
    with pytest.raises(SecretError):
        reject_secrets(url)
    reject_secrets(redact_credentials(url))


@pytest.mark.parametrize("text", [f"password={PW}", f"PASSWORD = '{PW}'", f"token={PW}xx", f"api_key={PW}", f"pwd={PW}"])
def test_key_value_secrets_are_redacted(text):
    from claimidx.security import redact, redact_credentials

    assert PW not in redact_credentials(text)
    assert PW not in redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "ssh://git@github.com/x",
        "SyntaxError: Unexpected token: }",
        "token=$GITHUB_TOKEN",
        "password authentication failed for user",
        "see claimidx://claim/abc",
    ],
)
def test_benign_text_is_unchanged(text):
    from claimidx.security import redact_credentials

    assert redact_credentials(text) == text


def test_fingerprint_is_computed_on_redacted_text():
    from claimidx.fingerprint import fingerprint, normalize_error
    from claimidx.security import redact_credentials

    assert PW not in normalize_error(CRED_ERR)
    assert fingerprint(err=CRED_ERR, eco="npm") == fingerprint(err=redact_credentials(CRED_ERR), eco="npm")
    # the password is not part of the identity: two passwords, one failure
    assert fingerprint(err=CRED_ERR, eco="npm") == fingerprint(err=CRED_ERR.replace(PW, "other1234"), eco="npm")


def test_driver_line_beats_success_banner_and_url_line():
    from claimidx.hook import _first_err_line

    assert _first_err_line("\n".join([BANNER, DRIVER, CRED_ERR])) == DRIVER
    assert _first_err_line("\n".join([BANNER, "WRONGPASS invalid username-password pair", CRED_ERR])) == "WRONGPASS invalid username-password pair"


def test_only_credential_line_is_redacted_everywhere(tmp_path, monkeypatch):
    """PaxReceipts shape: the only line with an error word carries the credential URL."""
    from claimidx.env import last_failure_path
    from claimidx.hook import _first_err_line
    from claimidx.runwrap import after_run
    from claimidx.store import Store

    monkeypatch.setenv("CLAIMIDX_LAST_FAILURE", str(tmp_path / "lf.json"))
    monkeypatch.setenv("CLAIMIDX_SESSION", "sess_test0726")
    output = "\n".join([BANNER, CRED_ERR, CRED_ERR]) + "\n"
    err = _first_err_line(output)
    assert err and "Redis Error" in err and PW not in err
    store = Store(str(tmp_path / "ix.sqlite"))
    out = after_run(store, ["node", "server.js", f"--url=redis://user:{PW}@db.internal:6379"], 1, output, cwd=str(tmp_path), emit=False)
    blob = json.dumps(out, default=str)
    assert PW not in blob
    lf = last_failure_path().read_text(encoding="utf-8")
    assert "pass" not in lf.lower()
    assert PW not in lf
    db = (tmp_path / "ix.sqlite").read_bytes()
    assert PW.encode() not in db


def test_claim_err_and_draft_and_preview_are_redacted(tmp_path, monkeypatch):
    from claimidx.claim import _publish_argv, render_draft
    from claimidx.fingerprint import fingerprint, fingerprint_accepted
    from claimidx.home import format_commons_review
    from claimidx.models import Claim, EvalSpec, Fix

    c = Claim(
        fp=fingerprint(err=CRED_ERR, eco="npm"),
        cls="other",
        err=CRED_ERR,
        eco="npm",
        fix=Fix(k="config", b="rotate the redis password"),
        eval=EvalSpec(cmd="npm test"),
        own="did:claimidx:t0726",
        note=f"password={PW}",
    )
    assert PW not in c.err and PW not in c.note
    assert fingerprint_accepted(c.fp, err=c.err, cls=c.cls, eco=c.eco, rt=c.rt, dep=c.dep)
    assert PW not in format_commons_review(c)
    assert PW not in _publish_argv(CRED_ERR, "config", "x", "npm test", "npm", "", [], "")
    draft = {
        "ok": True,
        "err": c.err,
        "cls": "other",
        "eco": "npm",
        "rt": "",
        "dep": [],
        "fix_k": "config",
        "fix_b": "x",
        "eval": "npm test",
        "eval_proof": True,
        "inferred": {},
        "warn": [],
        "publish_argv": "claimidx publish",
    }
    assert PW not in render_draft(draft)


def test_mcp_tool_output_is_redacted():
    from claimidx.mcp_server import _text

    out = _text({"output": CRED_ERR, "nested": [{"stderr": f"password={PW}"}]})
    assert PW not in json.dumps(out)


def test_full_commons_ledger_parses_with_zero_skips_and_unchanged_fps():
    """Every commons row still recomputes its stored fp, and redaction is a no-op on all of them."""
    from claimidx.home import parse_ledger
    from claimidx.security import redact_credentials

    rows = 0
    for name in ("claims.jsonl", "claims-claimidx.jsonl"):
        text = (ROOT / "data" / name).read_text(encoding="utf-8")
        raws = [json.loads(line) for line in text.splitlines() if line.strip()]
        claims, skipped = parse_ledger(text)
        assert skipped == [], skipped[:5]
        assert [c.fp for c in claims] == [r["fp"] for r in raws]
        for r in raws:
            assert redact_credentials(r["err"]) == r["err"]
            assert redact_credentials(r.get("note") or "") == (r.get("note") or "")
        rows += len(raws)
    assert rows >= 460
