from __future__ import annotations

import re
import subprocess
from pathlib import Path

from claimidx.discovery import ROOT


TEXT_SUFFIXES = {"", ".css", ".html", ".json", ".md", ".ps1", ".py", ".sh", ".toml", ".txt", ".xml", ".yml", ".yaml"}
FORBIDDEN_PATH_PARTS = {"enterprise", "pricing", "checkout", "customer", "billing", "social", "worker", "bot"}
FORBIDDEN_TEXT = (
    "remedy" + "ai",
    "rdna" + "vm",
    "old-" + "remedy",
    "har" + "per",
    "ben" + "jamin",
    "lu" + "cas",
    "sales" + "@claimidx.com",
    "support" + "@claimidx.com",
    "contact" + "@claimidx.com",
    "security" + "@claimidx.com",
    "home.claimidx.com/" + "operator",
    "claimidx.com/" + "pricing",
    "claimidx.com/" + "enterprise",
)
EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")


def _tracked() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [ROOT / line for line in result.stdout.splitlines() if line]


def test_tracked_tree_has_no_private_or_business_surfaces() -> None:
    paths = _tracked()
    for path in paths:
        if not path.exists():
            continue
        rel = path.relative_to(ROOT).as_posix().lower()
        assert not (set(Path(rel).parts) & FORBIDDEN_PATH_PARTS), rel
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for forbidden in FORBIDDEN_TEXT:
            assert forbidden not in lowered, (rel, forbidden)
        emails = {value.lower() for value in EMAIL.findall(text)}
        unexpected = {value for value in emails if not value.endswith("@example.com") and "@users.noreply.github.com" not in value}
        assert not unexpected, (rel, sorted(unexpected))
