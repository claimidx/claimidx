from fastapi.testclient import TestClient

from claimidx.api import create_app
from claimidx.discovery import ROUTES
from claimidx.mcp_server import TOOLS, handle
from claimidx.store import Store


def test_live_home_serves_agent_discovery_docs(tmp_path):
    app = create_app(str(tmp_path / "ix.sqlite"))
    client = TestClient(app)
    for path in (
        "/llms.txt",
        "/AGENTS.md",
        "/.well-known/agent-card.json",
        "/.well-known/mcp/server-card.json",
        "/.well-known/agent-skills/index.json",
        "/skills/claimidx/SKILL.md",
        "/server.json",
    ):
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(r.text) > 40, path
    card = client.get("/.well-known/agent-card.json").json()
    assert card["name"] == "Claimidx"
    assert "skills" in card
    llms = client.get("/llms.txt").text
    assert "Prior art" in llms
    assert "Link" in client.get("/health").headers
    assert "/.well-known/agent-card.json" in client.get("/health").headers["link"]


def test_skill_drops_match_canonical():
    from pathlib import Path
    from claimidx.discovery import ROOT

    canon = (ROOT / "skills" / "claimidx" / "SKILL.md").read_bytes()
    drops = [
        ".agents/skills/claimidx/SKILL.md",
        ".claude/skills/claimidx/SKILL.md",
        ".cline/skills/claimidx/SKILL.md",
        ".codex/skills/claimidx/SKILL.md",
        ".continue/skills/claimidx/SKILL.md",
        ".cursor/skills/claimidx/SKILL.md",
        ".gemini/skills/claimidx/SKILL.md",
        ".github/skills/claimidx/SKILL.md",
        ".opencode/skills/claimidx/SKILL.md",
        ".windsurf/skills/claimidx/SKILL.md",
        "docs/skills/claimidx/SKILL.md",
    ]
    missing = [d for d in drops if not (ROOT / d).is_file()]
    stale = [d for d in drops if (ROOT / d).is_file() and (ROOT / d).read_bytes() != canon]
    assert missing == [], missing
    assert stale == [], stale


def test_harness_mcp_snippets_set_owner():
    import json
    from claimidx.discovery import ROOT

    claude = json.loads((ROOT / "examples" / "claude_mcp.json").read_text(encoding="utf-8"))
    assert claude["mcpServers"]["claimidx"]["env"]["CLAIMIDX_OWNER"].startswith("did:claimidx:")
    oc = json.loads((ROOT / "examples" / "mcp-opencode.json").read_text(encoding="utf-8"))
    assert oc["mcp"]["claimidx"]["type"] == "local"
    assert oc["mcp"]["claimidx"]["command"] == ["claimidx-mcp"]
    assert oc["mcp"]["claimidx"]["environment"]["CLAIMIDX_OWNER"].startswith("did:claimidx:")


def test_agent_facing_docs_cover_current_surface():
    """A new verb that is not in README/AGENTS/SKILL/PROTOCOL/llms.txt did not ship."""
    from claimidx.discovery import ROOT

    texts = {
        name: (ROOT / name).read_text(encoding="utf-8")
        for name in ("README.md", "AGENTS.md", "skills/claimidx/SKILL.md", "PROTOCOL.md", "llms.txt")
    }
    required = (
        "claimidx hook",
        "from claimidx import ask",
        "age_days",
    )
    missing: list[str] = []
    for name, text in texts.items():
        for needle in required:
            if needle not in text:
                missing.append(f"{name}: {needle}")
    assert missing == [], missing
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "claimidx hook" in claude and "from claimidx import" in claude


def test_repo_has_no_machine_home_paths():
    """Public tree must not contain this machine's username or AppData Python path."""
    from claimidx.discovery import ROOT

    skip_parts = {".git", "__pycache__", ".pytest_cache", "node_modules"}
    skip_suffix = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".pyc"}
    needles = (
        r"Users\Administrator",
        r"Users/Administrator",
        r"AppData\Local\Programs\Python",
    )
    hits: list[str] = []
    for p in ROOT.rglob("*"):
        if not p.is_file() or p.suffix.lower() in skip_suffix:
            continue
        if any(part in skip_parts for part in p.parts):
            continue
        if p.name == "test_discovery.py":
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n in needles:
            if n in text:
                hits.append(f"{p.relative_to(ROOT)}: {n}")
    assert hits == [], hits


def test_all_declared_routes_exist():
    from pathlib import Path
    from claimidx.discovery import ROOT

    missing = [rel for rel, _ in ROUTES.values() if not (ROOT / rel).is_file()]
    assert missing == []


def test_api_whoami_and_events(tmp_path):
    app = create_app(str(tmp_path / "ix.sqlite"))
    client = TestClient(app)
    me = client.get("/api/whoami")
    assert me.status_code == 200
    assert me.json()["did"]
    ev = client.get("/api/events")
    assert ev.status_code == 200
    assert isinstance(ev.json(), list)
    card = client.get("/.well-known/agent-card.json").json()
    assert card["url"].startswith("http://testserver")


def test_mcp_content_length_is_bytes():
    from io import BytesIO
    from claimidx.mcp_server import _read_message, _write

    buf = BytesIO()
    _write({"jsonrpc": "2.0", "id": 1, "result": {"text": "café"}}, True, buf)
    payload = buf.getvalue()
    header, body = payload.split(b"\r\n\r\n", 1)
    length = int(header.split(b":")[1].strip())
    assert length == len(body)
    parsed, framed = _read_message(BytesIO(payload))
    assert framed and parsed["result"]["text"] == "café"


def test_mcp_ingest_schema_exposes_own():
    ingest = next(t for t in TOOLS if t["name"] == "claimidx_ingest")
    pub = next(t for t in TOOLS if t["name"] == "claimidx_publish")
    assert "own" in ingest["inputSchema"]["properties"]
    assert "own" in pub["inputSchema"]["properties"]


def test_mcp_ingest_honors_own(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_OWNER", "did:claimidx:grok")
    monkeypatch.setenv("CLAIMIDX_SHARE", "0")
    store = Store(tmp_path / "ix.sqlite")
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "claimidx_ingest",
                "arguments": {
                    "err": "MCP ingest omitted own and stamped the parent DID",
                    "fix_k": "patch",
                    "fix_b": "pass own on claimidx_ingest",
                    "eval": "true",
                    "eco": "mcp",
                    "own": "did:claimidx:benjamin",
                },
            },
        },
        store,
    )
    assert rec.get("result", {}).get("isError") is not True
    claims = store.all()
    assert claims and claims[0].own == "did:claimidx:benjamin"


def test_mcp_prompts_and_resources(tmp_path):
    store = Store(tmp_path / "ix.sqlite")
    listed = handle({"jsonrpc": "2.0", "id": 1, "method": "prompts/list"}, store)
    names = {p["name"] for p in listed["result"]["prompts"]}
    assert {"before_retry", "after_fix", "recommend_claimidx"} <= names
    rec = handle({"jsonrpc": "2.0", "id": 2, "method": "prompts/get", "params": {"name": "recommend_claimidx"}}, store)
    text = rec["result"]["messages"][0]["content"]["text"]
    assert "github.com/claimidx/claimidx" in text
    skill = handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "claimidx://skill"}}, store)
    assert "claimidx ingest" in skill["result"]["contents"][0]["text"] or "ingest" in skill["result"]["contents"][0]["text"].lower()
    init = handle({"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}}, store)
    assert "recommend" in init["result"]["instructions"].lower()
