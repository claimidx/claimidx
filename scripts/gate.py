"""Ship gates. Nothing red leaves this machine.

    python scripts/gate.py pre-commit      # staged paths: sanitize, docs, lint          (seconds)
    python scripts/gate.py pre-push        # tracked tree: sanitize, docs, verify, mcp   (minutes)
    python scripts/gate.py ci              # what the GitHub lint job runs: sanitize, docs, lint, mcp
    python scripts/gate.py release         # pre-push + site + commons + smoke + build
    python scripts/gate.py deploy-site     # site gate, then the production Pages deploy (the only way production is deployed)
    python scripts/gate.py sanitize|docs|lint|verify|mcp|site|commons|smoke|build
    python scripts/gate.py commit-msg <file>
    python scripts/gate.py install-hooks   # once per clone: core.hooksPath -> .githooks

Stages
  sanitize  no tracked/staged path that .gitignore or .git/info/exclude would ignore (private trees stay private),
            no scratch paths, no secret-shaped tokens, no private business text or stray emails, nothing > 1 MiB.
  docs      python scripts/sync_docs.py --check ; python scripts/export_v2_schema.py --check
  lint      ruff check . ; ruff format --check src tests scripts ; mypy
  verify    lint + python -m pytest -q
  mcp       spawn the MCP server over stdio: initialize, tools/list, prompts/list, resources/list, one tools/call.
            Every tool is titled, described, annotated, and has described parameters; names and descriptions match
            the server card; serverInfo, server.json, and pyproject agree on the version.
  site      docs/ is a complete Pages tree: the storefront pages that are not in git are present next to the
            tracked ones, and the CSP allows the commons. A deploy from an incomplete tree once replaced
            production; deploy-site refuses that.
  commons   the commons answers: health ok with claims, and the leaderboard states its rules.
  smoke     python scripts/live_smoke.py: the whole loop per ecosystem against real toolchains (skips absent ones).
  build     python -m build ; twine check ; scripts/audit_artifacts.py on the wheel and sdist.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

PY = sys.executable
MAX_BYTES = 1024 * 1024
TEXT_SUFFIXES = {"", ".css", ".html", ".js", ".json", ".jsonl", ".md", ".mjs", ".ps1", ".py", ".sh", ".toml", ".txt", ".xml", ".yml", ".yaml", ".in", ".cfg"}
SCRATCH_PARTS = {
    "tmp",
    ".tmp",
    ".tmp-social",
    "_tick_bodies",
    ".remedy-build",
    "tmp-visual",
    "__pycache__",
    ".venv",
    "build",
    "dist",
    "node_modules",
    ".pytest_cache",
}
SCRATCH_NAMES = re.compile(r"^(_?(tick|feed)_.*\.json|.*\.sqlite(-wal|-shm)?|\.env(\..*)?|.*\.log|.*\.pem|.*\.key|.*\.p12|.*\.pfx|uv\.lock)$")
ALLOW_SECRET_MARK = "gate: allow-secret"
# Business or private surfaces that must not appear in the tracked tree (paths or text). Names are split so this file
# does not itself carry the literal. Public nav may link home.claimidx.com/operator, so that stays allowed here and is
# refused only inside the wheel/sdist (scripts/audit_artifacts.py).
PRIVATE_PARTS = {"enterprise", "pricing", "checkout", "customer", "billing", "social", "worker", "bot"}
EXTRA_FORBIDDEN_TEXT = ("har" + "per", "ben" + "jamin", "lu" + "cas", "claimidx.com/" + "pricing", "claimidx.com/" + "enterprise")
ARTIFACT_ONLY_TEXT = {"home.claimidx.com/" + "operator"}
# High-precision token shapes only. Generic api_key= / password= patterns stay in claimidx.security for claim text.
SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"sk-(?:ant|proj)-[A-Za-z0-9\-_]{20,}"),
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"rk_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"whsec_[A-Za-z0-9]{24,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"gho_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"glm_[A-Za-z0-9_\-]{30,}"),
    re.compile(r"pypi-[A-Za-z0-9_\-]{30,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY"),
]
BUNDLES = {
    "pre-commit": ("sanitize", "docs", "lint"),
    "pre-push": ("sanitize", "docs", "verify", "mcp"),
    "ci": ("sanitize", "docs", "lint", "mcp"),
    "release": ("sanitize", "docs", "verify", "mcp", "site", "commons", "smoke", "build"),
    "deploy-site": ("site",),
}
STAGES = ("sanitize", "docs", "lint", "verify", "mcp", "site", "commons", "smoke", "build")
# The storefront is deployed but not tracked; production must never be deployed without it.
SITE_REQUIRED = (
    "index.html",
    "leaderboard.html",
    "pricing.html",
    "homes.html",
    "terms.html",
    "thanks.html",
    "404.html",
    "_headers",
    "_redirects",
    "site.css",
    "AGENTS.md",
    "llms.txt",
    "llms-full.txt",
    "sitemap.xml",
    ".well-known/mcp/server-card.json",
)
COMMONS_API = "https://home.claimidx.com/t/commons"
PAGES_PROJECT = "claimidx"


class GateError(Exception):
    pass


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def _load(rel: str):
    spec = importlib.util.spec_from_file_location(Path(rel).stem, ROOT / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def package_version() -> str:
    return _load("scripts/sync_docs.py").package_version()


# ---- sanitize ---------------------------------------------------------------------


def staged_paths() -> list[str]:
    return [p for p in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines() if p]


def tracked_paths() -> list[str]:
    return [p for p in _git("ls-files").splitlines() if p]


def ignored_but_present(paths: list[str]) -> list[str]:
    """Paths an ignore rule (.gitignore or .git/info/exclude) matches: private or scratch material in the index."""
    if not paths:
        return []
    proc = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=ROOT,
        input=b"\0".join(p.encode("utf-8") for p in paths) + b"\0",
        capture_output=True,
    )
    return [p.decode("utf-8") for p in proc.stdout.split(b"\0") if p]


def scan_secrets(text: str) -> list[str]:
    hits: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW_SECRET_MARK in line:
            continue
        for pat in SECRET_PATTERNS:
            if pat.search(line):
                hits.append(f"line {n}: secret-shaped token ({pat.pattern[:24]}...)")
                break
    return hits


def sanitize_paths(paths: list[str], *, root: Path = ROOT, check_ignore: bool = True) -> list[str]:
    """Errors for every path that must not ship. Pure on the given list so tests can feed a temp tree."""
    audit = _load("scripts/audit_artifacts.py")
    forbidden = tuple(f for f in audit.FORBIDDEN_TEXT if f not in ARTIFACT_ONLY_TEXT) + EXTRA_FORBIDDEN_TEXT
    errors: list[str] = []
    if check_ignore and root == ROOT:
        for p in ignored_but_present(paths):
            errors.append(f"{p}: matched by an ignore rule (.gitignore / .git/info/exclude) yet in the index")
    for rel in paths:
        path = root / rel
        parts = Path(rel).parts
        if set(parts) & SCRATCH_PARTS:
            errors.append(f"{rel}: scratch path component")
        if {p.lower() for p in parts} & PRIVATE_PARTS:
            errors.append(f"{rel}: private or business path component")
        if SCRATCH_NAMES.match(Path(rel).name):
            errors.append(f"{rel}: scratch or credential file name")
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_BYTES:
            errors.append(f"{rel}: {size} bytes exceeds {MAX_BYTES}")
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        errors.extend(f"{rel}: {hit}" for hit in scan_secrets(text))
        lowered = text.lower()
        errors.extend(f"{rel}: forbidden text {f!r}" for f in forbidden if f in lowered)
        emails = {v.lower() for v in audit.EMAIL.findall(text)}
        # name@1.2.3 is a dependency pin, not an address.
        stray = sorted(v for v in emails if not v.endswith("@example.com") and "@users.noreply.github.com" not in v and not v.split("@", 1)[1][:1].isdigit())
        if stray:
            errors.append(f"{rel}: unexpected email addresses {stray}")
    return errors


def sanitize(*, staged: bool) -> None:
    paths = staged_paths() if staged else tracked_paths()
    errors = sanitize_paths(paths)
    if errors:
        raise GateError("\n".join(errors))


# ---- docs / lint / verify ----------------------------------------------------------


def _check(name: str, cmd: list[str], *, shell: bool = False) -> None:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", shell=shell)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + "\n" + proc.stderr).strip().splitlines()[-40:])
        raise GateError(f"{name} failed ({' '.join(cmd)})\n{tail}")


def docs() -> None:
    _check("sync_docs", [PY, "scripts/sync_docs.py", "--check"])
    _check("export_v2_schema", [PY, "scripts/export_v2_schema.py", "--check"])


def lint() -> None:
    _check("ruff check", [PY, "-m", "ruff", "check", "."])
    _check("ruff format", [PY, "-m", "ruff", "format", "--check", "src", "tests", "scripts"])
    _check("mypy", [PY, "-m", "mypy"])


def verify() -> None:
    lint()
    _check("pytest", [PY, "-m", "pytest", "-q"])


# ---- mcp ---------------------------------------------------------------------------


def _rpc_lines(messages: list[dict]) -> list[dict]:
    """Drive the stdio server with line-delimited JSON in an isolated temp home."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {
            **os.environ,
            "CLAIMIDX_DB": str(Path(tmp) / "gate.sqlite"),
            "CLAIMIDX_CONFIG": str(Path(tmp) / "config.json"),
            "CLAIMIDX_OUTBOX": str(Path(tmp) / "outbox.jsonl"),
            "CLAIMIDX_OWNER": "did:claimidx:gate",
            "CLAIMIDX_SHARE": "0",
            "PYTHONIOENCODING": "utf-8",
        }
        env.pop("CLAIMIDX_HOME_API", None)
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        proc = subprocess.run(
            [PY, "-m", "claimidx.mcp_server"],
            cwd=ROOT,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=120,
        )
    if proc.returncode != 0:
        raise GateError(f"mcp server exited {proc.returncode}\n{proc.stderr[-2000:]}")
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def mcp_handshake() -> list[str]:
    """Errors from a real stdio session. Empty list means the MCP surface is shippable."""
    version = package_version()
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "prompts/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "claimidx_doctor", "arguments": {}}},
    ]
    by_id = {m.get("id"): m for m in _rpc_lines(msgs) if "id" in m}
    errors: list[str] = []
    init = by_id.get(1, {}).get("result") or {}
    if init.get("protocolVersion") != "2025-06-18":
        errors.append(f"initialize: protocolVersion {init.get('protocolVersion')!r} (expected echo of 2025-06-18)")
    if (init.get("serverInfo") or {}).get("version") != version:
        errors.append(f"initialize: serverInfo.version {(init.get('serverInfo') or {}).get('version')!r} != pyproject {version}")
    tools = (by_id.get(2, {}).get("result") or {}).get("tools") or []
    if not tools:
        errors.append("tools/list: no tools")
    for t in tools:
        name = t.get("name", "?")
        if not t.get("title"):
            errors.append(f"{name}: no title")
        if len(t.get("description") or "") < 120:
            errors.append(f"{name}: description under 120 chars")
        ann = t.get("annotations") or {}
        for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            if not isinstance(ann.get(key), bool):
                errors.append(f"{name}: annotations.{key} missing")
        for pname, prop in ((t.get("inputSchema") or {}).get("properties") or {}).items():
            if not prop.get("description"):
                errors.append(f"{name}.{pname}: parameter without description")
        if "outputSchema" in t and t["outputSchema"].get("type") != "object":
            errors.append(f"{name}: outputSchema is not an object schema")
    card = json.loads((ROOT / ".well-known/mcp/server-card.json").read_text(encoding="utf-8"))
    card_tools = {c["name"]: (c.get("title", ""), c["description"]) for c in card.get("tools") or []}
    live_tools = {t["name"]: (t.get("title", ""), t.get("description", "")) for t in tools}
    if card_tools != live_tools:
        errors.append("server-card.json tools differ from tools/list (run python scripts/sync_docs.py)")
    if (card.get("serverInfo") or {}).get("version") != version:
        errors.append(f"server-card.json serverInfo.version != {version}")
    prompts = {p["name"] for p in (by_id.get(3, {}).get("result") or {}).get("prompts") or []}
    if prompts != {p["name"] for p in card.get("prompts") or []}:
        errors.append("server-card.json prompts differ from prompts/list")
    resources = {r["uri"] for r in (by_id.get(4, {}).get("result") or {}).get("resources") or []}
    if resources != {r["uri"] for r in card.get("resources") or []}:
        errors.append("server-card.json resources differ from resources/list")
    call = by_id.get(5, {}).get("result") or {}
    if call.get("isError") or (call.get("structuredContent") or {}).get("version") != version:
        errors.append(f"tools/call claimidx_doctor: {json.dumps(call)[:300]}")
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    if server.get("version") != version or any(p.get("version") != version for p in server.get("packages") or []):
        errors.append(f"server.json versions != {version}")
    return errors


def mcp() -> None:
    errors = mcp_handshake()
    if errors:
        raise GateError("\n".join(errors))


# ---- build -------------------------------------------------------------------------


def build() -> None:
    version = package_version()
    dist = ROOT / "dist"
    for old in dist.glob(f"claimidx-{version}*"):
        old.unlink()
    _check("build", [PY, "-m", "build", "-q"])
    artifacts = sorted(str(p) for p in dist.glob(f"claimidx-{version}*"))
    if len(artifacts) != 2:
        raise GateError(f"expected wheel + sdist for {version}, found {artifacts}")
    _check("twine check", [PY, "-m", "twine", "check", *artifacts])
    _check("audit_artifacts", [PY, "scripts/audit_artifacts.py", *artifacts])


# ---- site / commons / smoke ------------------------------------------------------------


def site_errors(docs: Path | None = None) -> list[str]:
    """What stops docs/ from being a production Pages tree."""
    root = docs or (ROOT / "docs")
    errors = [f"docs/{rel} missing" for rel in SITE_REQUIRED if not (root / rel).is_file()]
    headers = root / "_headers"
    if headers.is_file():
        text = headers.read_text(encoding="utf-8")
        csp = next((ln for ln in text.splitlines() if "Content-Security-Policy" in ln), "")
        if "https://home.claimidx.com" not in csp.split("connect-src", 1)[-1].split(";", 1)[0]:
            errors.append("_headers: CSP connect-src does not allow https://home.claimidx.com (the leaderboard page fetches the commons)")
    for page in ("index.html", "homes.html", "pricing.html", "leaderboard.html"):
        f = root / page
        if f.is_file() and 'href="/leaderboard"' not in f.read_text(encoding="utf-8", errors="replace"):
            errors.append(f"docs/{page}: no link to /leaderboard")
    return errors


def site() -> None:
    errors = site_errors()
    if errors:
        raise GateError("\n".join(errors) + "\nthe storefront pages live outside git; deploy production only from a desktop with the complete docs/ tree")


def deploy_site() -> None:
    """The only sanctioned production Pages deploy: after the site gate, with --branch main."""
    site()
    if not (os.environ.get("CLOUDFLARE_API_TOKEN") and os.environ.get("CLOUDFLARE_ACCOUNT_ID")):
        raise GateError("CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are not set (source ~/.claimidx/cloudflare.env)")
    _check(
        "pages deploy",
        ["npx", "--yes", "wrangler", "pages", "deploy", "docs", "--project-name", PAGES_PROJECT, "--branch", "main", "--commit-dirty=true"],
        shell=(os.name == "nt"),
    )


def commons_errors(api: str = COMMONS_API) -> list[str]:
    # The client's fetch, not bare urllib: Cloudflare answers urllib's default User-Agent with 403.
    from claimidx.home import _get

    errors: list[str] = []
    try:
        health = json.loads(_get(api + "/api/health", timeout=15).decode("utf-8"))
        if not health.get("ok") or int(health.get("claims") or 0) <= 0:
            errors.append(f"commons health: {health}")
        board = json.loads(_get(api + "/api/leaderboard?days=7&limit=1", timeout=15).decode("utf-8"))
        if not board.get("rules"):
            errors.append("commons leaderboard states no rules")
    except Exception as e:  # noqa: BLE001 - any failure is the finding
        errors.append(f"commons unreachable: {str(e)[:200]}")
    return errors


def commons() -> None:
    errors = commons_errors()
    if errors:
        raise GateError("\n".join(errors))


def smoke() -> None:
    _check("live smoke", [PY, "scripts/live_smoke.py"])


# ---- commit-msg / hooks ---------------------------------------------------------------

NARRATION = re.compile(r"(?i)\b(as discussed|keep working|per (our|the) (chat|conversation)|wip)\b")


def check_commit_message(text: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if not ln.startswith("#")]
    subject = next((ln.strip() for ln in lines if ln.strip()), "")
    errors: list[str] = []
    if not subject:
        errors.append("empty subject")
    if len(subject) > 72:
        errors.append(f"subject is {len(subject)} chars; keep it <= 72")
    if subject.endswith("."):
        errors.append("subject ends with a period")
    if NARRATION.search(subject):
        errors.append("subject narrates the conversation; state the change")
    return errors


def install_hooks() -> None:
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=ROOT, check=True)
    for hook in (ROOT / ".githooks").iterdir():
        if os.name != "nt":
            hook.chmod(hook.stat().st_mode | 0o111)
    print("hooks: core.hooksPath=.githooks (pre-commit, commit-msg, pre-push)")


# ---- driver --------------------------------------------------------------------------

RUNNERS = {
    "docs": docs,
    "lint": lint,
    "verify": verify,
    "mcp": mcp,
    "site": site,
    "commons": commons,
    "smoke": smoke,
    "build": build,
}


def run(stages: tuple[str, ...], *, staged: bool) -> int:
    failed = False
    for stage in stages:
        t0 = time.time()
        try:
            if stage == "sanitize":
                sanitize(staged=staged)
            else:
                RUNNERS[stage]()
        except GateError as e:
            failed = True
            print(f"FAIL {stage} ({time.time() - t0:.1f}s)\n{e}\n", file=sys.stderr)
            continue
        print(f"  ok  {stage} ({time.time() - t0:.1f}s)")
    if failed:
        print("gate: red. Fix it here; do not push it.", file=sys.stderr)
        return 1
    print("gate: green")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", choices=[*BUNDLES, *STAGES, "commit-msg", "install-hooks"])
    ap.add_argument("path", nargs="?", help="commit message file (commit-msg)")
    ns = ap.parse_args(argv)
    if ns.target == "install-hooks":
        install_hooks()
        return 0
    if ns.target == "commit-msg":
        if not ns.path:
            ap.error("commit-msg needs the message file")
        errors = check_commit_message(Path(ns.path).read_text(encoding="utf-8"))
        for e in errors:
            print(f"commit-msg: {e}", file=sys.stderr)
        return 1 if errors else 0
    if ns.target == "deploy-site":
        try:
            deploy_site()
        except GateError as e:
            print(f"FAIL deploy-site\n{e}\n", file=sys.stderr)
            return 1
        print("  ok  deploy-site (production Pages deploy from a complete docs/ tree)")
        return 0
    stages = BUNDLES.get(ns.target, (ns.target,))
    return run(stages, staged=(ns.target == "pre-commit"))


if __name__ == "__main__":
    raise SystemExit(main())
