"""scripts/gate.py is the ship rule in code: hooks, CI, and docs all point at it, and it catches what must not leave."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from claimidx.discovery import ROOT


def _gate():
    spec = importlib.util.spec_from_file_location("gate", ROOT / "scripts" / "gate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hooks_call_the_gate():
    for hook, bundle in (("pre-commit", "pre-commit"), ("pre-push", "pre-push"), ("commit-msg", "commit-msg")):
        text = (ROOT / ".githooks" / hook).read_text(encoding="utf-8")
        assert text.startswith("#!/bin/sh"), hook
        assert f"scripts/gate.py {bundle}" in text, hook


def test_ci_runs_the_same_gate():
    """The GitHub lint job runs the ci bundle, so local and CI cannot drift."""
    yml = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "python scripts/gate.py ci" in yml
    assert "pytest -q" in yml  # matrix pytest stays explicit per Python version


def test_bundles_cover_every_gate_family():
    gate = _gate()
    assert set(gate.BUNDLES["pre-push"]) >= {"sanitize", "docs", "verify", "mcp"}
    assert set(gate.BUNDLES["ci"]) >= {"sanitize", "docs", "lint", "mcp"}
    assert set(gate.BUNDLES["release"]) >= set(gate.BUNDLES["pre-push"]) | {"build"}
    assert set(gate.BUNDLES["pre-commit"]) >= {"sanitize", "docs", "lint"}


def test_sanitize_catches_scratch_secrets_size_and_business_text(tmp_path: Path):
    gate = _gate()
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "note.txt").write_text("scratch", encoding="utf-8")
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "leak.py").write_text("TOKEN = '" + "ghp_" + "A" * 30 + "'\n", encoding="utf-8")
    (tmp_path / "allowed.py").write_text("TOKEN = '" + "ghp_" + "A" * 30 + "'  # gate: allow-secret\n", encoding="utf-8")
    (tmp_path / "big.bin").write_bytes(b"\0" * (gate.MAX_BYTES + 1))
    (tmp_path / "mail.md").write_text("write to sales" + "@claimidx.com\n", encoding="utf-8")
    (tmp_path / "_feed_tick.json").write_text("{}", encoding="utf-8")
    (tmp_path / "index.sqlite").write_bytes(b"")
    paths = ["tmp/note.txt", "ok.py", "leak.py", "allowed.py", "big.bin", "mail.md", "_feed_tick.json", "index.sqlite"]
    errors = gate.sanitize_paths(paths, root=tmp_path, check_ignore=False)
    joined = "\n".join(errors)
    assert "tmp/note.txt: scratch path component" in joined
    assert "leak.py: line 1: secret-shaped token" in joined
    assert "allowed.py" not in joined
    assert "big.bin" in joined and "exceeds" in joined
    assert "mail.md: forbidden text" in joined
    assert "_feed_tick.json: scratch or credential file name" in joined
    assert "index.sqlite: scratch or credential file name" in joined
    assert "ok.py" not in joined


def test_tracked_tree_passes_sanitize():
    """The gate that pre-push runs must be green on the committed tree."""
    gate = _gate()
    assert gate.sanitize_paths(gate.tracked_paths()) == []


def test_commit_message_rule():
    gate = _gate()
    assert gate.check_commit_message("Add ship gates\n\nWhy: nothing red leaves.\n") == []
    assert gate.check_commit_message("") == ["empty subject"]
    assert any("72" in e for e in gate.check_commit_message("x" * 73))
    assert any("period" in e for e in gate.check_commit_message("Add thing."))
    assert any("narrates" in e for e in gate.check_commit_message("keep working on stuff"))
    assert gate.check_commit_message("# comment only\nAdd thing\n") == []


def test_mcp_stdio_handshake_is_shippable():
    """A real stdio session: titled, described, annotated tools that match the server card and pyproject version."""
    assert _gate().mcp_handshake() == []


def test_contributing_and_briefs_name_the_gates():
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    assert "## Ship gates" in contributing
    for needle in ("python scripts/gate.py install-hooks", "gate.py pre-push", "gate.py release", "gate.py ci", "--no-verify"):
        assert needle in contributing, needle
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "scripts/gate.py" in claude


def test_site_gate_refuses_an_incomplete_pages_tree(tmp_path: Path):
    """The operator's site carries files git does not; a deploy without them once replaced production. The gate refuses that."""
    gate = _gate()
    docs = tmp_path / "docs"
    docs.mkdir()
    manifest = tmp_path / "site-required.txt"
    manifest.write_text("# operator files beyond git\nextra-a.html\nextra-b.html\n", encoding="utf-8")
    errors = gate.site_errors(docs, manifest)
    assert any("extra-a.html missing" in e for e in errors) and any("_headers missing" in e for e in errors)
    for rel in gate.site_required(manifest):
        f = docs / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('<a href="/leaderboard">x</a>' if rel.endswith(".html") else "{}", encoding="utf-8")
    (docs / "_headers").write_text("/*\n  Content-Security-Policy: default-src 'self'; connect-src 'self' https://pypi.org; img-src 'self'\n", encoding="utf-8")
    errors = gate.site_errors(docs, manifest)
    assert errors == ["_headers: CSP connect-src does not allow https://home.claimidx.com (the leaderboard page fetches the commons)"], errors
    (docs / "_headers").write_text(
        "/*\n  Content-Security-Policy: default-src 'self'; connect-src 'self' https://home.claimidx.com; img-src 'self'\n", encoding="utf-8"
    )
    assert gate.site_errors(docs, manifest) == []
    # The real tree passes only where the operator manifest and the files it names are present.
    real = gate.site_errors()
    assert real == [] or all("missing" in e for e in real), real


def test_release_bundle_includes_site_commons_and_smoke():
    gate = _gate()
    assert set(gate.BUNDLES["release"]) >= {"site", "commons", "smoke", "build"}
    assert gate.BUNDLES["deploy-site"] == ("site",)
    for stage in ("site", "commons", "smoke"):
        assert stage in gate.RUNNERS and stage in gate.STAGES
