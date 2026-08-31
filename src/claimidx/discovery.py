"""Agent-facing discovery documents served by a live home."""

from __future__ import annotations

from pathlib import Path

def _roots() -> list[Path]:
    here = Path(__file__).resolve()
    return [here.parents[2], Path.cwd(), Path.home() / ".claimidx"]


ROOT = _roots()[0]

# path → (file relative to repo root, media type)
ROUTES: dict[str, tuple[str, str]] = {
    "/llms.txt": ("llms.txt", "text/plain; charset=utf-8"),
    "/llms-full.txt": ("llms-full.txt", "text/plain; charset=utf-8"),
    "/ai.txt": ("ai.txt", "text/plain; charset=utf-8"),
    "/humans.txt": ("docs/humans.txt", "text/plain; charset=utf-8"),
    "/robots.txt": ("robots.txt", "text/plain; charset=utf-8"),
    "/AGENTS.md": ("AGENTS.md", "text/markdown; charset=utf-8"),
    "/server.json": ("server.json", "application/json"),
    "/.well-known/agent-card.json": (".well-known/agent-card.json", "application/a2a+json"),
    "/.well-known/agent.json": (".well-known/agent-card.json", "application/a2a+json"),
    "/.well-known/llms.txt": ("llms.txt", "text/plain; charset=utf-8"),
    "/.well-known/ai.txt": ("ai.txt", "text/plain; charset=utf-8"),
    "/.well-known/mcp.json": (".well-known/mcp.json", "application/json"),
    "/.well-known/mcp/server-card.json": (".well-known/mcp/server-card.json", "application/json"),
    "/.well-known/agent-skills/index.json": (".well-known/agent-skills/index.json", "application/json"),
    "/.well-known/agents.json": (".well-known/agents.json", "application/json"),
    "/.well-known/api-catalog": (".well-known/api-catalog", "application/linkset+json"),
    "/.well-known/security.txt": (".well-known/security.txt", "text/plain; charset=utf-8"),
    "/skills/claimidx/SKILL.md": ("skills/claimidx/SKILL.md", "text/markdown; charset=utf-8"),
    "/PROTOCOL.md": ("PROTOCOL.md", "text/markdown; charset=utf-8"),
}

LINK_HEADER = (
    '</.well-known/agent-card.json>; rel="describedby"; type="application/a2a+json", '
    '</llms.txt>; rel="alternate"; type="text/plain"; title="llms.txt", '
    '</.well-known/mcp/server-card.json>; rel="mcp", '
    '</.well-known/api-catalog>; rel="api-catalog"; type="application/linkset+json", '
    '</.well-known/agent-skills/index.json>; rel="describedby"; title="agent-skills"'
)


def resolve(url_path: str) -> tuple[Path, str] | None:
    spec = ROUTES.get(url_path)
    if not spec:
        return None
    rel, media = spec
    for root in _roots():
        path = root / rel
        if path.is_file():
            return path, media
    return None
