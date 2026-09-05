"""Docs that must move with the product, in one command.

    python scripts/sync_docs.py          # write the copies
    python scripts/sync_docs.py --check  # exit 1 if any copy is stale (CI)

Sources of truth: AGENTS.md, skills/claimidx/SKILL.md, PROTOCOL.md, README.md,
llms.txt, ai.txt, server.json, and root well-known discovery files.
Everything else here is a copy: the harness skill drops (.claude/, .cursor/, …),
docs/AGENTS.md, docs/llms.txt, and the llms-full.txt dumps crawlers fetch.

The release version has one source, ``pyproject.toml``. ``VERSION_STAMPS`` lists
every file that must carry a literal copy of it (``__version__``, A2A/MCP cards,
ai.txt, llms.txt); this script rewrites them so a release is a one-line bump.

The MCP server card's ``tools`` / ``prompts`` / ``resources`` arrays are rendered
from ``claimidx.mcp_server`` (``TOOLS``, ``PROMPTS``, ``RESOURCES``), so a tool
description edited in code reaches crawlers without a second hand edit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

SERVER_CARD = ".well-known/mcp/server-card.json"

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
    "llms.txt": ("docs/llms.txt",),
    "ai.txt": ("docs/ai.txt", "docs/.well-known/ai.txt"),
    "server.json": ("docs/server.json",),
    ".well-known/agent-card.json": ("docs/.well-known/agent-card.json",),
    ".well-known/agent.json": ("docs/.well-known/agent.json",),
    ".well-known/agents.json": ("docs/.well-known/agents.json",),
    ".well-known/mcp.json": ("docs/.well-known/mcp.json",),
    ".well-known/mcp/server-card.json": ("docs/.well-known/mcp/server-card.json",),
    ".well-known/api-catalog": ("docs/.well-known/api-catalog",),
    ".well-known/security.txt": ("docs/.well-known/security.txt",),
    ".well-known/agent-skills/index.json": ("docs/.well-known/agent-skills/index.json",),
    SKILL: tuple(f"{d}/skills/claimidx/SKILL.md" for d in SKILL_DROPS),
}

FULL_TARGETS = ("llms-full.txt", "docs/llms-full.txt")

PYPROJECT_VERSION = re.compile(r'(?m)^version = "([^"]+)"')

# (path, pattern) — group 1 is the version literal to rewrite. Every match in the
# file is stamped, so server.json's two `"version"` keys move together.
# `"version":` does not match `"protocolVersion":` (the quote is required).
_JSON_VERSION = r'"version": "([^"]+)"'
VERSION_STAMPS: tuple[tuple[str, str], ...] = (
    ("src/claimidx/__init__.py", r'(?m)^__version__ = "([^"]+)"'),
    ("server.json", _JSON_VERSION),
    (".well-known/agent-card.json", _JSON_VERSION),
    (".well-known/agent.json", _JSON_VERSION),
    (".well-known/agents.json", _JSON_VERSION),
    (".well-known/mcp.json", _JSON_VERSION),
    (".well-known/mcp/server-card.json", _JSON_VERSION),
    ("a2a/agent-card.json", _JSON_VERSION),
    ("ai.txt", r"(?m)^version: (\S+)$"),
    ("llms.txt", r"Use (\d+\.\d+\.\d+)\+"),
    ("docs/ARCHITECTURE.md", r"(?m)^## Current \(v([^)]+)\)"),
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def package_version() -> str:
    m = PYPROJECT_VERSION.search(_read("pyproject.toml"))
    if not m:
        raise SystemExit("pyproject.toml: version missing")
    return m.group(1)


def stamped() -> dict[str, str]:
    """Each VERSION_STAMPS file with every version literal set to the pyproject version."""
    version = package_version()
    out: dict[str, str] = {}
    for rel, pattern in VERSION_STAMPS:
        body = _read(rel)
        if not re.search(pattern, body):
            raise SystemExit(f"{rel}: no version literal matches {pattern!r}")

        def _sub(m: re.Match[str]) -> str:
            head = m.group(0)[: m.start(1) - m.start(0)]
            tail = m.group(0)[m.end(1) - m.start(0) :]
            return head + version + tail

        out[rel] = re.sub(pattern, _sub, body)
    return out


def render_full() -> str:
    return "\n\n".join(f"# FILE {rel}\n\n{_read(rel).rstrip()}" for rel in FULL_SOURCES) + "\n"


def render_server_card(current: str) -> str:
    """server-card.json with tools/prompts/resources taken from the MCP runtime lists."""
    from claimidx.mcp_server import PROMPTS, RESOURCES, TOOLS

    card = json.loads(current)
    card["tools"] = [{"name": t["name"], "title": t.get("title", ""), "description": t["description"]} for t in TOOLS]
    card["prompts"] = [{"name": p["name"], "description": p["description"]} for p in PROMPTS]
    card["resources"] = [{"uri": r["uri"], "name": r["name"], "description": r["description"]} for r in RESOURCES]
    return json.dumps(card, indent=2, ensure_ascii=False) + "\n"


def expected() -> dict[str, str]:
    out: dict[str, str] = dict(stamped())
    out[SERVER_CARD] = render_server_card(out.get(SERVER_CARD) or _read(SERVER_CARD))
    for src, targets in COPIES.items():
        body = out.get(src) or _read(src)
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
