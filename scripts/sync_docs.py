"""Docs that must move with the product, in one command.

    python scripts/sync_docs.py          # write the copies
    python scripts/sync_docs.py --check  # exit 1 if any copy is stale (CI)

Sources of truth: AGENTS.md, skills/claimidx/SKILL.md, PROTOCOL.md, README.md,
llms.txt, ai.txt, server.json, and root well-known discovery files.
Everything else here is a copy: the harness skill drops (.claude/, .cursor/, …),
docs/AGENTS.md, docs/PROTOCOL.md, docs/SECURITY.md, docs/llms.txt, and the llms-full.txt dumps crawlers fetch.

The release version has one source, ``pyproject.toml``. ``VERSION_STAMPS`` lists
every file that must carry a literal copy of it (``__version__``, A2A/MCP cards,
ai.txt, llms.txt); this script rewrites them so a release is a one-line bump.

The public site's product-brief line, current-release install pins, and footer
copyright year are stamped the same way (``data-cix-brief`` on the homepage,
``claimidx[server]>=`` pins on site HTML, ``data-cix-year`` on every copyright
line). ``deploy-site`` restamps a temporary copy of ``docs/`` with today's month
and year so a monthly redeploy stays current without a commit. A brief month more
than 35 days old, or a copyright year that is not the current year, fails the
site gate. The copyright stamp is ``© 2026`` in 2026 and ``© 2026–<year>``
(en dash) from 2027 on.

The MCP server card's ``tools`` / ``prompts`` / ``resources`` arrays are rendered
from ``claimidx.mcp_server`` (``TOOLS``, ``PROMPTS``, ``RESOURCES``), so a tool
description edited in code reaches crawlers without a second hand edit.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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

# src/claimidx/data/ copies ship in the wheel so MCP resources answer offline from a pip install.
COPIES = {
    "AGENTS.md": ("docs/AGENTS.md", "src/claimidx/data/AGENTS.md"),
    "PROTOCOL.md": ("src/claimidx/data/PROTOCOL.md", "docs/PROTOCOL.md"),
    "SECURITY.md": ("docs/SECURITY.md",),
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
    SKILL: tuple(f"{d}/skills/claimidx/SKILL.md" for d in SKILL_DROPS) + ("src/claimidx/data/SKILL.md",),
    # `claimidx serve` GET / falls back to this packaged copy when web/ is not beside the package (a pip install).
    "web/index.html": ("src/claimidx/data/inspector.html",),
}

FULL_TARGETS = ("llms-full.txt", "docs/llms-full.txt")

PYPROJECT_VERSION = re.compile(r'(?m)^version = "([^"]+)"')

# (path, pattern) — group 1 is the version literal to rewrite. Every match in the
# file is stamped. `"version":` does not match `"protocolVersion":` (the quote is required).
_JSON_VERSION = r'"version": "([^"]+)"'
# server.json: the top-level `version` (MCP Registry envelope) and packages[].version both
# follow pyproject; gate.py and the mcp-registry workflow require them to be equal.
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


# Public site stamps (homepage brief, current-release install pins, footer year).
# Historical "≥0.7.16" feature notes, the terms effective date, the SkillMaxx
# citation, and the "as of Oct 2026" status line are left alone.
STAMP_TZ = ZoneInfo("America/Chicago")
BRIEF_MAX_AGE_DAYS = 35
COPYRIGHT_START = 2026
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
BRIEF_RE = re.compile(r"(<span data-cix-brief>)(.*?)(</span>)", re.DOTALL)
BRIEF_VALUE_RE = re.compile(r"^v(.+?) · ([A-Z][a-z]+ \d{4})$")
SITE_PIN_RE = re.compile(r"(claimidx\[server\](?:>=|&gt;=)|INSTALL · SHARE · ≥)(\d+\.\d+\.\d+)")
YEAR_RE = re.compile(r"(<span data-cix-year>)(.*?)(</span>)", re.DOTALL)
# Footer copyright: © 2026 in the start year, © 2026–YYYY (en dash) afterwards.
YEAR_VALUE_RE = re.compile(r"^© 2026(?:–\d{4})?$")


def stamp_now(when: date | None = None) -> date:
    """Calendar date for the brief month, in the operator's timezone."""
    return when or datetime.now(STAMP_TZ).date()


def brief_label(version: str, when: date | None = None) -> str:
    day = stamp_now(when)
    return f"v{version} · {MONTHS[day.month - 1]} {day.year}"


def copyright_label(when: date | None = None) -> str:
    """© 2026 in 2026; © 2026–<year> (en dash) from 2027 on, America/Chicago."""
    year = stamp_now(when).year
    if year <= COPYRIGHT_START:
        return f"© {COPYRIGHT_START}"
    return f"© {COPYRIGHT_START}–{year}"


def stamp_site_text(body: str, version: str, when: date | None = None) -> str:
    """Fill data-cix-brief, data-cix-year, and current-release install pins in one HTML page."""
    label = brief_label(version, when)
    year_label = copyright_label(when)
    body = BRIEF_RE.sub(lambda m: f"{m.group(1)}{label}{m.group(3)}", body)
    body = YEAR_RE.sub(lambda m: f"{m.group(1)}{year_label}{m.group(3)}", body)
    return SITE_PIN_RE.sub(lambda m: f"{m.group(1)}{version}", body)


def _parse_brief_month(month_year: str) -> date | None:
    try:
        return datetime.strptime(month_year, "%B %Y").date().replace(day=1)
    except ValueError:
        return None


def site_stamp_errors(body: str, rel: str, version: str, when: date | None = None) -> list[str]:
    """Version mismatch, a missing marker, a brief month older than 35 days, or a stale copyright year."""
    day = stamp_now(when)
    errors: list[str] = []
    if "Product brief" in body and not BRIEF_RE.search(body):
        errors.append(f"{rel}: Product brief is not wrapped in <span data-cix-brief>")
    if ("©" in body or "&copy;" in body) and not YEAR_RE.search(body):
        errors.append(f"{rel}: copyright line is not wrapped in <span data-cix-year>")
    expected_year = copyright_label(day)
    for m in YEAR_RE.finditer(body):
        inner = m.group(2).strip()
        if not YEAR_VALUE_RE.fullmatch(inner):
            errors.append(f"{rel}: copyright stamp {inner!r} is not '© 2026' or '© 2026–YYYY'")
        elif inner != expected_year:
            errors.append(f"{rel}: copyright stamp {inner!r} != {expected_year}")
    for m in BRIEF_RE.finditer(body):
        inner = m.group(2).strip()
        parsed = BRIEF_VALUE_RE.fullmatch(inner)
        if not parsed:
            errors.append(f"{rel}: brief stamp {inner!r} is not 'vVERSION · Month Year'")
            continue
        got, month_year = parsed.group(1), parsed.group(2)
        if got != version:
            errors.append(f"{rel}: brief version v{got} != package {version}")
        start = _parse_brief_month(month_year)
        if start is None:
            errors.append(f"{rel}: brief month {month_year!r} is not a Month Year")
        elif start > day:
            errors.append(f"{rel}: brief month {month_year} is in the future")
        else:
            age = (day - start).days
            if age > BRIEF_MAX_AGE_DAYS:
                errors.append(f"{rel}: brief month {month_year} is {age} days old (limit {BRIEF_MAX_AGE_DAYS})")
    for m in SITE_PIN_RE.finditer(body):
        if m.group(2) != version:
            errors.append(f"{rel}: install pin {m.group(2)} != package {version}")
    return errors


def site_html_rels() -> tuple[str, ...]:
    """docs/ HTML pages that carry a product brief, a copyright line, or a current-release install pin."""
    docs = ROOT / "docs"
    rels: list[str] = []
    if not docs.is_dir():
        return ()
    for path in sorted(docs.rglob("*.html")):
        body = path.read_text(encoding="utf-8")
        if BRIEF_RE.search(body) or SITE_PIN_RE.search(body) or YEAR_RE.search(body) or "Product brief" in body or "©" in body or "&copy;" in body:
            rels.append(path.relative_to(ROOT).as_posix())
    return tuple(rels)


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
    check_errors: list[str] = []
    for rel, body in expected().items():
        path = ROOT / rel
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if current == body:
            continue
        stale.append(rel)
        if not ns.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8", newline="\n")
    version = package_version()
    # Write mode restamps pins and the brief month to today. --check requires the
    # marker, a matching version, and matching install pins; month freshness is
    # enforced by the site / deploy-site gate so regular CI does not go red when
    # the calendar month rolls over without a commit.
    for rel in site_html_rels():
        path = ROOT / rel
        current = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else ""
        stamped_body = stamp_site_text(current, version)
        pin_and_marker_errors = [
            e for e in site_stamp_errors(current, rel, version) if "days old" not in e and "is in the future" not in e and "copyright stamp" not in e
        ]
        if ns.check:
            check_errors.extend(pin_and_marker_errors)
        elif current != stamped_body:
            stale.append(rel)
            path.write_text(stamped_body, encoding="utf-8", newline="\n")
    if ns.check:
        for rel in stale:
            print(f"stale: {rel}", file=sys.stderr)
        for err in check_errors:
            print(err, file=sys.stderr)
        return 1 if stale or check_errors else 0
    for rel in stale:
        print(f"wrote {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
