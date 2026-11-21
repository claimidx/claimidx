"""0.7.29: Windows path separators and UTF-8 subprocess decoding, catchable on Linux."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from claimidx.binding import _rel_tokens
from claimidx.distinct import compare
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.home import parse_ledger
from claimidx.loopkey import scrub_line

ROOT = Path(__file__).resolve().parents[1]


def test_full_commons_ledger_parses_with_zero_skips_and_unchanged_fps():
    """Pull of the shipped ledgers stays skipped==[] and fingerprints stay put."""
    rows = 0
    for name in ("claims.jsonl", "claims-claimidx.jsonl"):
        text = (ROOT / "data" / name).read_text(encoding="utf-8")
        raws = [json.loads(line) for line in text.splitlines() if line.strip()]
        claims, skipped = parse_ledger(text)
        assert skipped == [], skipped[:5]
        assert [c.fp for c in claims] == [r["fp"] for r in raws]
        rows += len(raws)
    assert rows >= 460


def test_drive_letter_forward_slash_matches_backslash():
    bs = normalize_error(r"failed at C:\Users\runner\app\page.tsx")
    fwd = normalize_error("failed at C:/Users/runner/app/page.tsx")
    lower = normalize_error(r"failed at c:\Users\runner\app\page.tsx")
    other = normalize_error(r"failed at D:\data\app\page.tsx")
    other_fwd = normalize_error("failed at D:/data/app/page.tsx")
    assert bs == fwd == "failed at <PATH>"
    assert lower == bs
    assert other == other_fwd == "failed at <PATH>"
    assert fingerprint(err=r"failed at C:\Users\runner\app\page.tsx") == fingerprint(err="failed at C:/Users/runner/app/page.tsx")


def test_relative_backslash_paths_match_forward_slash():
    assert normalize_error(r"failed at .\src\page.tsx") == normalize_error("failed at ./src/page.tsx")
    assert normalize_error(r"failed at ..\lib\page.tsx") == normalize_error("failed at ../lib/page.tsx")
    assert "<PATH>" in normalize_error(r"failed at .\src\page.tsx")
    assert "<PATH>" in normalize_error(r"failed at ..\lib\page.tsx")


def test_scheme_urls_are_not_treated_as_windows_drives():
    # A naive [A-Za-z]:/ rule would turn claimidx:// into claimid<PATH>.
    raw = "MCP resources claimidx://skill claimidx://agents claimidx://protocol"
    assert normalize_error(raw) == raw


def test_stored_maven_windows_path_placeholder_is_stable():
    # Packaged ledger row already stores the 0.7.28 normalization of /C:/...
    raw = "[ERROR] /C:<PATH>:[<N>,<N>] package org.apache.commons.lang3 does not exist"
    assert normalize_error(raw) == raw


def test_windows_temp_paths_scrub_across_slash_styles():
    a = r"boom at 2026-11-21T15:00:00Z in C:\Users\joe\AppData\Local\Temp\abc123\work pid=4412: failed"
    b = r"boom at 2026-11-22T16:11:22Z in C:\Users\sue\AppData\Local\Temp\xyz789\work pid=9981: failed"
    c = "boom at 2026-11-21T15:00:00Z in C:/Users/joe/AppData/Local/Temp/abc123/work pid=4412: failed"
    assert scrub_line(a) == scrub_line(b) == scrub_line(c)
    assert "<TMP>" in scrub_line(a)


def test_binding_resolves_backslash_relative_tokens(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("print(1)\n", encoding="utf-8")
    assert _rel_tokens(["python", "src/app.py"], tmp_path) == ["src/app.py"]
    assert _rel_tokens(["python", r"src\app.py"], tmp_path) == ["src/app.py"]


def test_distinct_decodes_non_utf8_output_with_replace(tmp_path: Path):
    result = compare(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'hello \\xe9 world\\n')"],
        [sys.executable, "-c", "print('other')"],
        cwd=str(tmp_path),
    )
    assert result["a"].get("error") == ""
    assert result["exit"] == 0
    assert result["result"] == "distinct"
    blob = (result["a"]["stdout"] or "") + (result["a"]["raw"] or "")
    assert "hello" in blob
    assert "\ufffd" in blob
    assert result["a"]["returncode"] == 0
