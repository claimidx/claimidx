"""Docs that must move with the product, in one command.

    python scripts/sync_docs.py          # write the copies
    python scripts/sync_docs.py --check  # exit 1 if any copy is stale (CI)

Sources of truth: AGENTS.md, skills/claimidx/SKILL.md, PROTOCOL.md, README.md, llms.txt.
Everything else here is a copy: the harness skill drops (.claude/, .cursor/, …),
docs/AGENTS.md, docs/llms.txt, and the llms-full.txt dumps crawlers fetch.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKILL = "skills/claimidx/SKILL.md"
FULL_SOURCES = ("AGENTS.md", SKILL, "PROTOCOL.md", "README.md")

SKILL_DROPS = (
    ".agents",
    ".claude",
    ".cline",
    ".codex",
    ".continue",
    ".cursor",
    ".gemini",
    ".github",
    ".opencode",
    ".windsurf",
    "docs",
)

COPIES = {
    "AGENTS.md": ("docs/AGENTS.md",),
    "ENTERPRISE.md": ("docs/ENTERPRISE.md",),
    "llms.txt": ("docs/llms.txt",),
    SKILL: tuple(f"{d}/skills/claimidx/SKILL.md" for d in SKILL_DROPS),
}

FULL_TARGETS = ("llms-full.txt", "docs/llms-full.txt")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def render_full() -> str:
    return "\n\n".join(f"# FILE {rel}\n\n{_read(rel).rstrip()}" for rel in FULL_SOURCES) + "\n"


def expected() -> dict[str, str]:
    out: dict[str, str] = {}
    for src, targets in COPIES.items():
        body = _read(src)
        for t in targets:
            out[t] = body
    full = render_full()
    for t in FULL_TARGETS:
        out[t] = full
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="report stale copies; write nothing")
    ns = ap.parse_args(argv)
    stale: list[str] = []
    for rel, body in expected().items():
        path = ROOT / rel
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if current == body:
            continue
        stale.append(rel)
        if not ns.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8", newline="\n")
    if ns.check:
        for rel in stale:
            print(f"stale: {rel}", file=sys.stderr)
        return 1 if stale else 0
    for rel in stale:
        print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
