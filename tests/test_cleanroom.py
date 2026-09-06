"""`claim --yes` proves fix.b in a clean clone before it mints nr."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from claimidx.cli import main

BROKEN = "import json\n\ndef load(t):\n    return json.loads(t, encoding='utf-8')\n"
FIXED = "import json\n\ndef load(t):\n    return json.loads(t)\n"
ERR = "TypeError: loads() got an unexpected keyword argument 'encoding'"
EVAL = "python -c \"import mod; mod.load('{}')\""


def _repo(tmp_path: Path) -> Path:
    tree = tmp_path / "repo"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / "mod.py").write_text(BROKEN, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "broken"], cwd=tree, check=True)
    return tree


def _claim(db: str, tree: Path, capsys, *extra: str) -> dict:
    rc = main(["--db", db, "--fmt", "json", "claim", "--err", ERR, "--cwd", str(tree), "--eval", EVAL, "--yes", *extra])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0, out
    return out


def _nr(db: str, cid: str, capsys) -> int:
    assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
    return int(json.loads(capsys.readouterr().out)["nr"])


def test_uncommitted_patch_is_proven_in_a_clean_clone(tmp_path: Path, capsys):
    tree = _repo(tmp_path)
    (tree / "mod.py").write_text(FIXED, encoding="utf-8")  # the fix, not committed
    db = str(tmp_path / "ix.sqlite")
    out = _claim(db, tree, capsys)
    room = out["clean_room"]
    assert room["ran"] and room["before"]["held"] is False and room["applied"] and room["after_held"] and room["recorded"], room
    assert out["replay"]["recorded"] is True and _nr(db, out["id"], capsys) == 1
    # The working tree is untouched and the room is gone.
    assert (tree / "mod.py").read_text(encoding="utf-8") == FIXED
    assert not list(tmp_path.glob("cix-room-*"))


def test_a_fix_that_does_not_apply_records_nothing_and_says_so(tmp_path: Path, capsys):
    tree = _repo(tmp_path)
    (tree / "mod.py").write_text(FIXED, encoding="utf-8")
    bogus = "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1,4 +1,4 @@\n import json\n \n def load(t):\n-    return json.loads(t, encoding='latin-1')\n+    return json.loads(t)\n"
    db = str(tmp_path / "ix.sqlite")
    out = _claim(db, tree, capsys, "--fix", bogus, "--fix-k", "patch")
    room = out["clean_room"]
    assert room["ran"] and room["applied"] is False and "does not apply in a clean clone" in room["reason"], room
    assert not out.get("replay", {}).get("recorded") and _nr(db, out["id"], capsys) == 0
    assert any("clean clone" in w for w in out.get("warn") or []), out


def test_a_prose_fix_skips_the_room_and_says_the_hold_is_working_tree_only(tmp_path: Path, capsys):
    tree = _repo(tmp_path)
    (tree / "mod.py").write_text(FIXED, encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    out = _claim(db, tree, capsys, "--fix", "removed the encoding kwarg", "--fix-k", "patch")
    assert out["clean_room"]["ran"] is False and "not a diff" in out["clean_room"]["reason"]
    assert out["replay"]["recorded"] is True
    assert any("clean-room skipped" in w for w in out.get("warn") or []), out


def test_a_fix_already_committed_is_not_discriminating(tmp_path: Path, capsys):
    tree = _repo(tmp_path)
    (tree / "mod.py").write_text(FIXED, encoding="utf-8")
    diff = subprocess.run(["git", "diff", "--no-color"], cwd=tree, capture_output=True, text=True, check=True).stdout
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "fixed"], cwd=tree, check=True)
    db = str(tmp_path / "ix.sqlite")
    out = _claim(db, tree, capsys, "--fix", diff, "--fix-k", "patch")
    room = out["clean_room"]
    assert room["ran"] and room["before"]["held"] is True and "not discriminating" in room["reason"], room
    assert not out.get("replay", {}).get("recorded") and _nr(db, out["id"], capsys) == 0


def test_no_clean_room_flag_keeps_the_old_path(tmp_path: Path, capsys):
    tree = _repo(tmp_path)
    (tree / "mod.py").write_text(FIXED, encoding="utf-8")
    db = str(tmp_path / "ix.sqlite")
    out = _claim(db, tree, capsys, "--no-clean-room")
    assert "clean_room" not in out and out["replay"]["recorded"] is True


def test_room_maps_cwd_by_real_path(tmp_path: Path):
    """A cwd given as an 8.3 alias or in another case still lands inside the clone, not back in the tree."""
    import os

    from claimidx.cleanroom import _subdir

    tree = _repo(tmp_path)
    sub = tree / "pkg"
    sub.mkdir()
    assert _subdir(str(tree), str(tree)) == ""
    assert _subdir(str(sub), str(tree)) == "pkg"
    assert _subdir(str(sub).upper() if os.name == "nt" else str(sub), str(tree)) == "pkg"
    assert _subdir(str(tmp_path), str(tree)) == ""  # outside the tree: never escape the clone
    if os.name == "nt":
        import ctypes

        buf = ctypes.create_unicode_buffer(512)
        if ctypes.windll.kernel32.GetShortPathNameW(str(sub), buf, 512):
            assert _subdir(buf.value, str(tree)) == "pkg"
