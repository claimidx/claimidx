"""Every hold says how it was produced: asserted, replayed, clean-room, or applied.

A reader of an observation, local or on the commons, must not have to infer
from scattered fields whether a green check was a replay in the author's
tree, a fresh-clone proof, another agent's apply, or a bare assertion.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from claimidx import home
from claimidx.cli import main
from claimidx.identity import verify_record
from claimidx.store import Store

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
    (tree / "mod.py").write_text(FIXED, encoding="utf-8")
    return tree


def _modes(db: str, cid: str, capsys) -> list[tuple[str, bool, bool]]:
    assert main(["--db", db, "--fmt", "json", "explain", cid]) == 0
    graph = json.loads(capsys.readouterr().out)
    return [(o.get("mode"), o["held"], o["replayed"]) for o in graph["observations"]]


def test_each_way_of_holding_is_named_on_the_observation(tmp_path: Path, capsys):
    tree = _repo(tmp_path)
    db = str(tmp_path / "ix.sqlite")
    diff = subprocess.run(["git", "diff", "--no-color"], cwd=tree, capture_output=True, text=True, check=True).stdout
    assert main(["--db", db, "--fmt", "json", "claim", "--err", ERR, "--cwd", str(tree), "--eval", EVAL, "--yes", "--fix", diff, "--fix-k", "patch"]) == 0
    out = json.loads(capsys.readouterr().out)
    cid = out["id"]
    assert out["clean_room"]["recorded"]
    assert _modes(db, cid, capsys) == [("clean-room", True, True)]
    assert main(["--db", db, "--fmt", "json", "confirm", cid]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--fmt", "json", "confirm", "--replay", "--cwd", str(tree), cid]) == 0
    capsys.readouterr()
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(tree), str(clone)], check=True)
    assert main(["--db", db, "--fmt", "json", "apply", cid, "--cwd", str(clone), "--yes"]) == 0
    capsys.readouterr()
    assert _modes(db, cid, capsys) == [("clean-room", True, True), ("asserted", True, False), ("replayed", True, True), ("applied", True, True)]
    assert main(["--db", db, "--fmt", "json", "fail", cid]) in (0, 2)
    capsys.readouterr()
    assert _modes(db, cid, capsys)[-1] == ("asserted", False, False)


def test_mode_travels_in_the_signed_record(tmp_path: Path, monkeypatch):
    from claimidx.board import signed_observation

    rec = signed_observation("cix_00000000000000a1", held=True, replayed=True, own="did:claimidx:agent-b", mode="applied")
    assert rec["mode"] == "applied" and verify_record(rec)
    assert not verify_record(dict(rec, mode="clean-room"))  # the mode is signed too
    assert signed_observation("cix_00000000000000a1", held=True, replayed=True, own="x")["mode"] == "replayed"
    assert signed_observation("cix_00000000000000a1", held=True, replayed=False, own="x")["mode"] == "asserted"

    monkeypatch.setenv("CLAIMIDX_COMMONS", "1")
    store = Store(str(tmp_path / "ix.sqlite"))
    from claimidx.fingerprint import fingerprint
    from claimidx.models import Claim, EvalSpec, Fix

    err = "ModuleNotFoundError: No module named 'tomli'"
    c = store.put(
        Claim(
            fp=fingerprint(err=err, eco="py"),
            cls="module_not_found",
            err=err,
            eco="py",
            fix=Fix(k="pin", b="tomli==2.0.1"),
            eval=EvalSpec(cmd='python -c "import tomli"'),
            own="did:claimidx:agent-a",
        )
    )
    posts: list[dict] = []
    monkeypatch.setattr(home, "_post", lambda url, payload, token="", timeout=20.0: posts.append(payload) or {"claim": {}})
    home.share_claim(store, c)
    home.share_observation(store, c, held=True, actor="did:claimidx:agent-b", mode="applied")
    assert posts[-1]["mode"] == "applied" and posts[-1]["kind"] == "hold"
