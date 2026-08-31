from fastapi.testclient import TestClient

from claimidx.api import create_app
from claimidx.discovery import ROUTES
from claimidx.fingerprint import fingerprint, normalize_error
from claimidx.mcp_server import PROMPTS, RESOURCES, TOOLS, handle
from claimidx.models import Claim, EvalSpec, Fix
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
        "claimidx_hook",
        "from claimidx import ask",
        "age_days",
        "verify --dry-run",
    )
    missing: list[str] = []
    for name, text in texts.items():
        for needle in required:
            if needle not in text:
                missing.append(f"{name}: {needle}")
    assert missing == [], missing
    claude = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "claimidx hook" in claude and "from claimidx import" in claude
    assert "verify --dry-run" in claude
    copilot = (ROOT / ".github" / "copilot-instructions.md").read_text(encoding="utf-8")
    assert "verify --dry-run" in copilot
    for rel in ("llms-full.txt", "docs/llms-full.txt"):
        dump = (ROOT / rel).read_text(encoding="utf-8")
        assert "verify --dry-run" in dump, rel


def test_llms_full_embeds_current_briefings():
    """llms-full.txt is the dump crawlers fetch. Stale PROTOCOL.md omitted verify."""
    from claimidx.discovery import ROOT

    files = ("AGENTS.md", "skills/claimidx/SKILL.md", "PROTOCOL.md", "README.md")
    for dump_rel in ("llms-full.txt", "docs/llms-full.txt"):
        dump = (ROOT / dump_rel).read_text(encoding="utf-8")
        missing = [rel for rel in files if (ROOT / rel).read_text(encoding="utf-8").strip() not in dump]
        assert missing == [], f"{dump_rel}: {missing}"


def test_repo_has_no_machine_home_paths():
    """Tracked public tree must not contain this machine's username or AppData Python path."""
    import subprocess
    from claimidx.discovery import ROOT

    tracked = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    needles = (
        r"Users\Administrator",
        r"Users/Administrator",
        r"AppData\Local\Programs\Python",
    )
    hits: list[str] = []
    for rel in tracked:
        if rel.replace("\\", "/").endswith("tests/test_discovery.py"):
            continue
        p = ROOT / rel
        if not p.is_file() or p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2"}:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n in needles:
            if n in text:
                hits.append(f"{rel}: {n}")
    assert hits == [], hits


def test_all_declared_routes_exist():
    from pathlib import Path
    from claimidx.discovery import ROOT

    missing = [rel for rel, _ in ROUTES.values() if not (ROOT / rel).is_file()]
    assert missing == []


def test_static_agent_cards_do_not_advertise_loopback():
    """Public discovery JSON must not send crawlers to 127.0.0.1."""
    import json
    from claimidx.discovery import ROOT

    rels = (
        ".well-known/agent-card.json",
        ".well-known/agents.json",
        "a2a/agent-card.json",
        "server.json",
    )
    hits = [rel for rel in rels if "127.0.0.1" in (ROOT / rel).read_text(encoding="utf-8")]
    assert hits == [], hits
    agents = json.loads((ROOT / ".well-known" / "agents.json").read_text(encoding="utf-8"))
    assert "openapi_url" not in agents
    card = json.loads((ROOT / ".well-known" / "agent-card.json").read_text(encoding="utf-8"))
    assert card.get("supportedInterfaces") == []
    alias = ROOT / ".well-known" / "agent.json"
    assert alias.is_file()
    assert alias.read_bytes() == (ROOT / ".well-known" / "agent-card.json").read_bytes()
    a2a = json.loads((ROOT / "a2a" / "agent-card.json").read_text(encoding="utf-8"))
    assert a2a == card


def test_agent_briefing_recommends_pypi():
    """claimidx is on PyPI. Agent briefing must say pip install claimidx."""
    from claimidx.discovery import ROOT

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "pip install claimidx" in agents or 'pip install "claimidx' in agents
    for rel in ("README.md", "skills/claimidx/SKILL.md", "docs/AGENTS.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "pip install" in text and "claimidx" in text, rel


def test_pyproject_license_is_spdx_string():
    """PyPI metadata uses an SPDX string, not the deprecated TOML table."""
    from claimidx.discovery import ROOT

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "Apache-2.0"' in text
    assert "license-files" in text
    assert "license = { text" not in text


def test_llms_txt_names_pypi():
    """Agent docs index must name pip install claimidx now that PyPI is live."""
    from claimidx.discovery import ROOT

    text = (ROOT / "llms.txt").read_text(encoding="utf-8")
    assert "pypi.org/project/claimidx" in text
    assert "pip install" in text and "claimidx" in text
    assert "do not pin" in text.lower()
    assert "0.5.6" in text, "llms.txt must warn agents off leaked wheels"


def test_llms_txt_is_the_agent_index_not_the_storefront():
    """llms.txt / ai.txt are what agents fetch first. Storefront and sales@ stay off those pages."""
    from claimidx.discovery import ROOT

    for rel in ("llms.txt", "docs/llms.txt", "ai.txt", "docs/ai.txt", "docs/.well-known/ai.txt"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert "sales@" not in text, rel
        assert "claimidx.com/pricing" not in text, rel
        assert "claimidx.com/homes" not in text, rel
        assert "ENTERPRISE.md" in text, rel


def test_pypi_metadata_links_github_docs():
    """PyPI project.urls and README must point at GitHub docs, not relative paths that 404 on pypi.org."""
    from claimidx.discovery import ROOT

    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for needle in (
        "https://github.com/claimidx/claimidx/blob/main/AGENTS.md",
        "https://github.com/claimidx/claimidx/blob/main/PROTOCOL.md",
        "https://github.com/claimidx/claimidx/blob/main/SECURITY.md",
        "https://github.com/claimidx/claimidx/blob/main/skills/claimidx/SKILL.md",
        "https://raw.githubusercontent.com/claimidx/claimidx/main/data/claims.jsonl",
    ):
        assert needle in toml, needle
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://github.com/claimidx/claimidx/blob/main/AGENTS.md" in readme
    assert "](AGENTS.md)" not in readme
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    for n in ("AGENTS.md", "PROTOCOL.md", "SECURITY.md", "llms.txt", "skills/claimidx/SKILL.md", "schema/claim.v1.json"):
        assert n in manifest, n


def test_sdist_manifest_excludes_hangout_and_worker_probes():
    """The published sdist is protocol. Hangout bot and worker probes stay off PyPI."""
    from claimidx.discovery import ROOT

    text = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    missing = [
        n
        for n in (
            "test_social_directed.py",
            "test_worker_stripe.py",
            "social_reply.py",
            "worker_stripe_probe.mjs",
            "stripe_hook.py",
            "test_stripe_hook.py",
            "INTERNAL",
            "extras",
            "BOTS.md",
            "claimidx_ops.ps1",
        )
        if n not in text
    ]
    assert missing == [], missing
    assert "prune tests" not in text.lower() or "recursive-include tests" in text
    api = (ROOT / "src" / "claimidx" / "api.py").read_text(encoding="utf-8")
    assert not (ROOT / "src" / "claimidx" / "stripe_hook.py").exists()
    assert not any(line.startswith("from .stripe_hook import") or line.startswith("from claimidx.stripe_hook") for line in api.splitlines())


def test_wheel_excludes_stripe_hook(tmp_path):
    """pip installs the wheel. MANIFEST.in does not apply; stripe_hook must not live under src/claimidx."""
    import shutil
    import subprocess
    import sys
    import zipfile
    from claimidx.discovery import ROOT

    assert not (ROOT / "src" / "claimidx" / "stripe_hook.py").exists()
    assert (ROOT / "extras" / "stripe_hook.py").is_file()
    shutil.rmtree(ROOT / "build", ignore_errors=True)
    out = tmp_path / "dist"
    out.mkdir()
    # Isolated backend: CI's pytest env has no setuptools (build-system only).
    subprocess.check_call(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            str(ROOT),
            "-w",
            str(out),
            "--no-deps",
        ],
        cwd=tmp_path,
    )
    wheels = list(out.glob("claimidx-*.whl"))
    assert len(wheels) == 1, wheels
    names = zipfile.ZipFile(wheels[0]).namelist()
    assert not any(n.endswith("stripe_hook.py") for n in names)
    assert any(n.endswith("claimidx/cli.py") for n in names)
    assert not any("INTERNAL/" in n.replace("\\", "/") for n in names)


def test_mcp_server_json_is_honest():
    """MCP registry description is max 100 chars. Do not advertise a PyPI 404."""
    import json
    from claimidx.discovery import ROOT

    data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    assert 1 <= len(data["description"]) <= 100
    pkgs = data.get("packages") or []
    pypi = [p for p in pkgs if p.get("registryType") == "pypi"]
    assert pypi, "server.json must list the live PyPI package"
    assert pypi[0].get("identifier") == "claimidx"
    docs = ROOT / "docs" / "server.json"
    if docs.is_file():
        assert json.loads(docs.read_text(encoding="utf-8")) == data


def test_pypi_readme_has_mcp_registry_name():
    """Official MCP registry verifies PyPI ownership via mcp-name in the package README."""
    import json
    from claimidx.discovery import ROOT

    data = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    name = data["name"]
    assert name == "io.github.claimidx/claimidx"
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    marker = f"<!-- mcp-name: {name} -->"
    assert marker in readme, "PyPI README must carry the MCP registry ownership marker"


def test_mcp_registry_publish_uses_github_oidc():
    """Official MCP registry listing must not depend on a browser login or a PAT in git."""
    from claimidx.discovery import ROOT

    path = ROOT / ".github" / "workflows" / "mcp-registry.yml"
    assert path.is_file(), "mcp-registry.yml publishes server.json via GitHub OIDC"
    text = path.read_text(encoding="utf-8")
    assert "mcp-publisher login github-oidc" in text
    assert "mcp-publisher publish" in text
    assert "id-token: write" in text
    assert "pull_request_target" not in text
    assert "secrets." not in text


def test_mcp_registry_hides_leaked_pypi_versions():
    """0.5.6 leaked operated extras and was yanked. MCP registry must not leave it active."""
    from claimidx.discovery import ROOT

    text = (ROOT / ".github" / "workflows" / "mcp-registry.yml").read_text(encoding="utf-8")
    assert "mcp-publisher status" in text
    assert "--status deleted" in text
    assert "0.5.6" in text
    assert "io.github.claimidx/claimidx" in text
    assert "No changes to apply" in text, "re-dispatch must treat already-deleted as success"


def test_mcp_registry_publish_only_on_tags():
    """workflow_dispatch must hide leaked versions without re-publishing an existing tag."""
    from claimidx.discovery import ROOT

    text = (ROOT / ".github" / "workflows" / "mcp-registry.yml").read_text(encoding="utf-8")
    assert "  hide-leaked:" in text, "hide-leaked job must exist so dispatch can run it"
    pub, rest = text.split("  hide-leaked:", 1)
    assert "if: startsWith(github.ref, 'refs/tags/')" in pub
    assert "if: startsWith(github.ref, 'refs/tags/')" not in rest


def test_security_md_covers_published_wheel():
    """Agents pin PyPI versions. SECURITY.md must say the wheel is protocol-only."""
    from claimidx.discovery import ROOT

    text = (ROOT / "SECURITY.md").read_text(encoding="utf-8").lower()
    assert "wheel" in text, "SECURITY.md must mention the published wheel"
    assert "protocol-only" in text or "protocol only" in text
    assert "pypi" in text or "pip install" in text
    assert "0.5.6" in text, "do not pin the leaked 0.5.6 wheel"


def test_security_md_does_not_deny_shipped_webfonts():
    """SECURITY.md must not claim no webfonts if docs/_headers allows Google Fonts."""
    from claimidx.discovery import ROOT

    headers = (ROOT / "docs" / "_headers").read_text(encoding="utf-8")
    sec = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    if "fonts.googleapis.com" in headers or "fonts.gstatic.com" in headers:
        assert "No third-party scripts or webfonts" not in sec, headers


def test_sitemap_lists_machine_discovery():
    """Sitemap must list the machine URLs llms.txt already advertises."""
    from claimidx.discovery import ROOT

    text = (ROOT / "docs" / "sitemap.xml").read_text(encoding="utf-8")
    missing = [
        n
        for n in (
            "https://claimidx.com/llms.txt",
            "https://claimidx.com/.well-known/agent-card.json",
            "https://claimidx.com/.well-known/api-catalog",
            "https://claimidx.com/.well-known/security.txt",
            "https://claimidx.com/SECURITY.md",
            "https://claimidx.com/PROTOCOL.md",
            "https://claimidx.com/ENTERPRISE.md",
        )
        if n not in text
    ]
    assert missing == [], missing


def test_license_is_full_apache_2():
    """GitHub licensee needs the full Apache 2.0 terms, not the short appendix."""
    from claimidx.discovery import ROOT

    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    missing = [
        n
        for n in (
            "Apache License",
            "Version 2.0, January 2004",
            "Grant of Copyright License",
            "Grant of Patent License",
            "END OF TERMS AND CONDITIONS",
        )
        if n not in text
    ]
    assert missing == [], missing


def test_live_home_serves_protocol_and_api_catalog(tmp_path):
    app = create_app(str(tmp_path / "ix.sqlite"))
    client = TestClient(app)
    proto = client.get("/PROTOCOL.md")
    assert proto.status_code == 200
    assert "fingerprint" in proto.text.lower()
    alias = client.get("/.well-known/agent.json")
    assert alias.status_code == 200
    assert alias.json()["name"] == "Claimidx"
    catalog = client.get("/.well-known/api-catalog")
    assert catalog.status_code == 200
    body = catalog.json()
    hrefs = []
    for block in body.get("linkset") or []:
        for item in block.get("item") or []:
            hrefs.append(item.get("href") or "")
    joined = " ".join(hrefs)
    assert "agent-card.json" in joined
    assert "mcp/server-card.json" in joined
    assert "claims.jsonl" in joined


def test_api_catalog_lists_protocol_docs():
    """RFC 9727 catalog must list PROTOCOL.md, SECURITY.md, and security.txt."""
    import json
    from claimidx.discovery import ROOT

    body = json.loads((ROOT / ".well-known" / "api-catalog").read_text(encoding="utf-8"))
    hrefs = [
        item.get("href") or ""
        for block in body.get("linkset") or []
        for item in block.get("item") or []
    ]
    joined = " ".join(hrefs)
    missing = [
        n
        for n in (
            "https://claimidx.com/PROTOCOL.md",
            "https://claimidx.com/SECURITY.md",
            "https://claimidx.com/.well-known/security.txt",
        )
        if n not in joined
    ]
    assert missing == [], missing


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


def test_mcp_ask_missing_err_is_invalid_params(tmp_path):
    store = Store(tmp_path / "ix.sqlite")
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "claimidx_ask", "arguments": {}},
        },
        store,
    )
    err = rec.get("error") or {}
    assert err.get("code") == -32602
    assert "err" in (err.get("message") or "").lower() or "missing" in (err.get("message") or "").lower()


def test_mcp_ingest_schema_exposes_own():
    ingest = next(t for t in TOOLS if t["name"] == "claimidx_ingest")
    pub = next(t for t in TOOLS if t["name"] == "claimidx_publish")
    assert "own" in ingest["inputSchema"]["properties"]
    assert "own" in pub["inputSchema"]["properties"]


def test_mcp_server_card_lists_every_tool():
    """Crawlers that read the MCP card must see the same tools as tools/list."""
    import json
    from claimidx.discovery import ROOT

    names = {t["name"] for t in TOOLS}
    for rel in (
        ".well-known/mcp/server-card.json",
        "docs/.well-known/mcp/server-card.json",
    ):
        card = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        listed = {t["name"] for t in card.get("tools") or []}
        assert listed == names, f"{rel}: missing={sorted(names - listed)} extra={sorted(listed - names)}"


def test_mcp_server_card_lists_prompts_and_resources():
    """Crawlers that read the MCP card must see prompts/list and resources/list."""
    import json
    from claimidx.discovery import ROOT

    prompt_names = {p["name"] for p in PROMPTS}
    resource_uris = {r["uri"] for r in RESOURCES}
    for rel in (
        ".well-known/mcp/server-card.json",
        "docs/.well-known/mcp/server-card.json",
    ):
        card = json.loads((ROOT / rel).read_text(encoding="utf-8"))
        listed_p = {p["name"] for p in card.get("prompts") or []}
        listed_r = {r["uri"] for r in card.get("resources") or []}
        assert listed_p == prompt_names, f"{rel} prompts: missing={sorted(prompt_names - listed_p)} extra={sorted(listed_p - prompt_names)}"
        assert listed_r == resource_uris, f"{rel} resources: missing={sorted(resource_uris - listed_r)} extra={sorted(listed_r - resource_uris)}"


def test_api_version_matches_package():
    from claimidx import __version__
    from claimidx.api import create_app

    assert create_app().version == __version__


def test_discovery_cards_match_package_version():
    """A2A/MCP discovery JSON must advertise the same version as pyproject and __version__."""
    import json
    import re
    from claimidx import __version__
    from claimidx.discovery import ROOT

    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'(?m)^version = "([^"]+)"', toml)
    assert m, "pyproject version missing"
    assert m.group(1) == __version__
    cards = (
        (ROOT / ".well-known" / "agent-card.json", ("version",)),
        (ROOT / ".well-known" / "agent.json", ("version",)),
        (ROOT / "a2a" / "agent-card.json", ("version",)),
        (ROOT / ".well-known" / "mcp.json", ("version",)),
        (ROOT / ".well-known" / "mcp" / "server-card.json", ("serverInfo", "version")),
        (ROOT / "server.json", ("version",)),
        (ROOT / "docs" / ".well-known" / "agent-card.json", ("version",)),
        (ROOT / "docs" / ".well-known" / "mcp.json", ("version",)),
        (ROOT / "docs" / ".well-known" / "mcp" / "server-card.json", ("serverInfo", "version")),
        (ROOT / "docs" / "server.json", ("version",)),
    )
    for path, keys in cards:
        cur = json.loads(path.read_text(encoding="utf-8"))
        for k in keys:
            cur = cur[k]
        assert cur == __version__, f"{path}: {cur} != {__version__}"


def test_mcp_force_keeps_stored_cls(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMIDX_SHARE", "0")
    store = Store(tmp_path / "ix.sqlite")
    err = "TypeError: x is not a function"
    c = store.put(Claim(
        fp=fingerprint(err=err, cls="other", eco="npm"),
        cls="other",
        err=normalize_error(err),
        eco="npm",
        fix=Fix(k="patch", b="await x()"),
        eval=EvalSpec(cmd="true"),
        own="did:claimidx:test",
    ))
    rec = handle(
        {
            "jsonrpc": "2.0",
            "id": 21,
            "method": "tools/call",
            "params": {
                "name": "claimidx_ingest",
                "arguments": {
                    "err": err,
                    "eco": "npm",
                    "fix_k": "patch",
                    "fix_b": "await x().catch(()=>{})",
                    "eval": "true",
                    "force": True,
                    "own": "did:claimidx:test",
                },
            },
        },
        store,
    )
    assert rec.get("result", {}).get("isError") is not True
    again = store.get(c.id)
    assert again is not None
    assert again.cls == "other"
    assert "catch" in again.fix.b


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
    assert "pip install" in text and "claimidx" in text
    assert "git clone" not in text
    retry = handle({"jsonrpc": "2.0", "id": 5, "method": "prompts/get", "params": {"name": "before_retry", "arguments": {"err": "x"}}}, store)
    retry_text = retry["result"]["messages"][0]["content"]["text"]
    assert "verify --dry-run" in retry_text
    skill = handle({"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "claimidx://skill"}}, store)
    assert "claimidx ingest" in skill["result"]["contents"][0]["text"] or "ingest" in skill["result"]["contents"][0]["text"].lower()
    init = handle({"jsonrpc": "2.0", "id": 4, "method": "initialize", "params": {}}, store)
    assert "recommend" in init["result"]["instructions"].lower()
    assert "verify --dry-run" in init["result"]["instructions"]
