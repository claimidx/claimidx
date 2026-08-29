"""Bundled coding-agent failure claims. Fingerprints are computed, not invented."""

from __future__ import annotations

from datetime import datetime, timezone

from .fingerprint import classify, fingerprint, normalize_error
from .models import Claim, EvalSpec, Fix

_TS = datetime(2026, 8, 1, tzinfo=timezone.utc)

_SEEDS: list[dict] = [
    {"id": "spr_a11c000000000001", "err": "TypeError: params is a Promise", "eco": "npm", "rt": "node@20.18.2", "dep": ["next@15.0.0"], "tried": ["sync-access", "restart"], "fix": ("patch", "const { slug } = await params"), "eval": "npx tsc --noEmit", "st": "confirmed", "nc": 11, "nf": 1, "note": "Next 15 dynamic APIs are async."},
    {"id": "spr_a11c000000000002", "err": "TypeError: searchParams is a Promise", "eco": "npm", "rt": "node@20", "dep": ["next@15.0.0"], "tried": ["sync-access"], "fix": ("patch", "const sp = await searchParams"), "eval": "npx tsc --noEmit", "st": "confirmed", "nc": 7, "nf": 0, "note": "Same class as cookies()/headers() in Next 15."},
    {"id": "spr_a11c000000000003", "err": "ModuleNotFoundError: No module named 'pydantic_core'", "eco": "py", "rt": "py@3.12", "dep": ["pydantic@2.9.0"], "tried": ["pip-install-pydantic"], "fix": ("pin", "pydantic>=2.7,<3"), "eval": "python -c 'import pydantic'", "st": "confirmed", "nc": 4, "nf": 0},
    {"id": "spr_a11c000000000004", "err": "ValidationError: 1 validation error for Model\nurl\n  Field required [type=missing]", "eco": "py", "rt": "py@3.12", "dep": ["pydantic@2.8.0"], "tried": ["parse_obj"], "fix": ("patch", "Model.model_validate(data)  # not parse_obj"), "eval": "python -c 'import pydantic'", "st": "confirmed", "nc": 9, "nf": 1},
    {"id": "spr_a11c000000000005", "err": "npm ERR! ERESOLVE unable to resolve dependency tree", "eco": "npm", "rt": "node@20", "dep": ["react@19.0.0", "next@15.0.0"], "tried": ["npm-install"], "fix": ("config", "legacy-peer-deps=true"), "eval": "npm ls --depth=0", "st": "confirmed", "nc": 6, "nf": 2},
    {"id": "spr_a11c000000000006", "err": "Cannot find module '@/lib/utils' or its corresponding type declarations.", "eco": "npm", "rt": "node@20", "dep": ["typescript@5.6.0"], "tried": ["relative-import"], "fix": ("config", '{"compilerOptions":{"baseUrl":".","paths":{"@/*":["./src/*"]}}}'), "eval": "npx tsc --noEmit", "st": "confirmed", "nc": 8, "nf": 0},
    {"id": "spr_a11c000000000007", "err": "browserType.launch: Executable doesn't exist at /home/runner/.cache/ms-playwright/chromium", "eco": "browser", "rt": "node@20", "dep": ["playwright@1.48.0"], "tried": ["npm-install"], "fix": ("cmd", "npx playwright install --with-deps chromium"), "eval": "npx playwright --version", "st": "confirmed", "nc": 12, "nf": 0},
    {"id": "spr_a11c000000000008", "err": "ZodError: Required at \"email\"", "eco": "npm", "rt": "node@20", "dep": ["zod@3.23.0"], "tried": ["optional-field"], "fix": ("patch", "email: z.string().email()"), "eval": "npx tsc --noEmit", "st": "proposed", "nc": 1, "nf": 0},
    {"id": "spr_a11c000000000009", "err": "Error: 429 Too Many Requests — rate limit exceeded", "eco": "other", "rt": "", "dep": [], "tried": ["retry-immediate"], "fix": ("cmd", "sleep $((2 ** attempt)) && retry"), "eval": "true", "st": "confirmed", "nc": 5, "nf": 1},
    {"id": "spr_a11c00000000000a", "err": "StripeInvalidRequestError: Invalid API version", "eco": "npm", "rt": "node@20", "dep": ["stripe@16.0.0"], "tried": ["latest-sdk"], "fix": ("pin", "apiVersion: '2024-06-20'"), "eval": "node -e \"require('stripe')\"", "st": "confirmed", "nc": 3, "nf": 0},
    {"id": "spr_a11c00000000000b", "err": "Error: MCP error -32601: Method not found: tools/list", "eco": "mcp", "rt": "node@20", "dep": [], "tried": ["tools.list"], "fix": ("constraint", "speak JSON-RPC method tools/list after initialize"), "eval": "true", "st": "confirmed", "nc": 4, "nf": 0},
    {"id": "spr_a11c00000000000c", "err": "ModuleNotFoundError: No module named 'src'", "eco": "py", "rt": "py@3.12", "dep": [], "tried": ["sys-path-hack"], "fix": ("config", "pip install -e ."), "eval": "true", "st": "confirmed", "nc": 6, "nf": 1},
    {"id": "spr_a11c00000000000d", "err": "TypeError: useFormState is not exported from 'react-dom'", "eco": "npm", "rt": "node@20", "dep": ["react@19.0.0", "react-dom@19.0.0"], "tried": ["react-dom-import"], "fix": ("patch", "import { useActionState } from 'react'"), "eval": "npx tsc --noEmit", "st": "confirmed", "nc": 5, "nf": 0},
    {"id": "spr_a11c00000000000e", "err": "CssSyntaxError: @tailwind is not a known at-rule", "eco": "npm", "rt": "node@20", "dep": ["tailwindcss@4.0.0"], "tried": ["postcss-config"], "fix": ("patch", '@import "tailwindcss";'), "eval": "npx tailwindcss --help", "st": "confirmed", "nc": 4, "nf": 0},
    {"id": "spr_a11c00000000000f", "err": "go: verifying module: checksum mismatch", "eco": "go", "rt": "go@1.23", "dep": [], "tried": ["go-clean-modcache"], "fix": ("cmd", "go clean -modcache && go mod download"), "eval": "go mod verify", "st": "proposed", "nc": 2, "nf": 1},
    {"id": "spr_a11c000000000010", "err": "PermissionError: [Errno 13] Permission denied: '/usr/local/lib'", "eco": "py", "rt": "py@3.12", "dep": [], "tried": ["sudo-pip"], "fix": ("cmd", "python -m venv .venv && .venv/bin/pip install -e ."), "eval": "true", "st": "confirmed", "nc": 7, "nf": 0},
    {"id": "spr_a11c000000000011", "err": "Error: Cannot find module 'next/headers' from '/app/src/app/page.tsx'", "eco": "npm", "rt": "node@18", "dep": ["next@13.5.0"], "tried": ["pages-router-import"], "fix": ("constraint", "next/headers only exists in the app router"), "eval": "npx next build", "st": "contested", "nc": 1, "nf": 3},
    {"id": "spr_a11c000000000012", "err": "HTTP 200 OK but body is {\"error\":\"invalid_request\"}", "eco": "other", "rt": "", "dep": [], "tried": ["trust-status"], "fix": ("constraint", "do not treat HTTP 200 as success; parse body.error"), "eval": "true", "st": "confirmed", "nc": 3, "nf": 0},
    {"id": "spr_a11c000000000013", "err": "AttributeError: type object 'datetime.datetime' has no attribute 'utcnow'", "eco": "py", "rt": "py@3.12", "dep": [], "tried": ["datetime.utcnow"], "fix": ("patch", "datetime.now(timezone.utc)  # utcnow removed"), "eval": "true", "st": "confirmed", "nc": 8, "nf": 0, "note": "Python 3.12+."},
    {"id": "spr_a11c000000000014", "err": "ImportError: cannot import name 'BaseSettings' from 'pydantic'", "eco": "py", "rt": "py@3.12", "dep": ["pydantic@2.0.0"], "tried": ["from pydantic import BaseSettings"], "fix": ("patch", "from pydantic_settings import BaseSettings"), "eval": "true", "st": "confirmed", "nc": 10, "nf": 1, "note": "Moved in pydantic v2."},
    {"id": "spr_a11c000000000015", "err": "ImportError: HTTPX is required to use TestClient", "eco": "py", "rt": "py@3.12", "dep": ["starlette@0.41.0"], "tried": ["starlette.testclient"], "fix": ("pin", "httpx>=0.27"), "eval": "true", "st": "confirmed", "nc": 9, "nf": 0},
    {"id": "spr_a11c000000000016", "err": "TypeError: Client.__init__() got an unexpected keyword argument 'app'", "eco": "py", "rt": "py@3.13", "dep": ["httpx@0.28.0", "starlette@0.41.0"], "tried": ["TestClient"], "fix": ("pin", "Use httpx 0.27 or a Starlette that speaks httpx 0.28 (httpx2 extra)"), "eval": "true", "st": "confirmed", "nc": 6, "nf": 0},
    {"id": "spr_a11c000000000017", "err": "SyntaxError: f-string expression part cannot include a backslash", "eco": "py", "rt": "py@3.11", "dep": [], "tried": ["backslash-in-fstring"], "fix": ("patch", "Assign the expression to a name, then interpolate the name."), "eval": "true", "st": "confirmed", "nc": 7, "nf": 0},
    {"id": "spr_a11c000000000018", "err": "ModuleNotFoundError: No module named 'cgi'", "eco": "py", "rt": "py@3.13", "dep": [], "tried": ["import cgi"], "fix": ("constraint", "cgi was removed in 3.13; use email.message / multipart"), "eval": "true", "st": "confirmed", "nc": 5, "nf": 0},
    {"id": "spr_a11c000000000019", "err": "ReferenceError: __dirname is not defined in ES module scope", "eco": "npm", "rt": "node@20", "dep": [], "tried": ["__dirname"], "fix": ("patch", "import { fileURLToPath } from 'url'; const __dirname = fileURLToPath(new URL('.', import.meta.url))"), "eval": "true", "st": "confirmed", "nc": 11, "nf": 0},
    {"id": "spr_a11c00000000001a", "err": "You tried to access openai.ChatCompletion, but this is no longer supported in openai>=1.0.0", "eco": "py", "rt": "py@3.12", "dep": ["openai@1.0.0"], "tried": ["openai.ChatCompletion.create"], "fix": ("patch", "client.chat.completions.create(...)"), "eval": "true", "st": "confirmed", "nc": 14, "nf": 1},
    {"id": "spr_a11c00000000001b", "err": "SSLError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate", "eco": "py", "rt": "py@3.12", "dep": [], "tried": ["disable-verify"], "fix": ("pin", "pip install certifi and set SSL_CERT_FILE to certifi.where()"), "eval": "true", "st": "confirmed", "nc": 8, "nf": 2},
    {"id": "spr_a11c00000000001c", "err": "Error: listen EADDRINUSE: address already in use :::3000", "eco": "npm", "rt": "node@20", "dep": [], "tried": ["restart"], "fix": ("constraint", "Another process owns the port. Free it or set PORT to an unused one."), "eval": "true", "st": "confirmed", "nc": 6, "nf": 0},
    {"id": "spr_a11c00000000001d", "err": "npm ERR! enoent ENOENT: no such file or directory, open 'package.json'", "eco": "npm", "rt": "node@20", "dep": [], "tried": ["npm-install-wrong-dir"], "fix": ("constraint", "cd to the package root that actually contains package.json"), "eval": "true", "st": "confirmed", "nc": 9, "nf": 0},
    {"id": "spr_a11c00000000001e", "err": "Failed: async def functions are not natively supported. You may need pytest-asyncio", "eco": "py", "rt": "py@3.12", "dep": ["pytest@8.0.0"], "tried": ["pytest-async-def"], "fix": ("pin", "pytest-asyncio and asyncio_mode=auto"), "eval": "true", "st": "confirmed", "nc": 7, "nf": 0},
    {"id": "spr_a11c00000000001f", "err": "ReferenceError: fetch is not defined", "eco": "npm", "rt": "node@16", "dep": [], "tried": ["window-fetch"], "fix": ("pin", "Node 18+ has fetch, or npm install undici"), "eval": "true", "st": "confirmed", "nc": 8, "nf": 0},
    {"id": "spr_a11c000000000020", "err": "Error: Invalid src prop on next/image, hostname is not configured under images", "eco": "npm", "rt": "node@20", "dep": ["next@15.0.0"], "tried": ["img-tag"], "fix": ("config", "images.remotePatterns in next.config"), "eval": "true", "st": "confirmed", "nc": 6, "nf": 0},
    {"id": "spr_a11c000000000021", "err": "StripeSignatureVerificationError: No signatures found matching the expected signature for payload", "eco": "npm", "rt": "node@20", "dep": ["stripe@16.0.0"], "tried": ["JSON.stringify-body"], "fix": ("constraint", "verify with the raw request body, not parsed JSON"), "eval": "true", "st": "confirmed", "nc": 7, "nf": 1},
    {"id": "spr_a11c000000000022", "err": "The token '&&' is not a valid statement separator in this version.", "eco": "other", "rt": "windows", "dep": [], "tried": ["bash-and-and"], "fix": ("constraint", "Windows PowerShell 5: use ; not &&. PowerShell 7 accepts &&."), "eval": "true", "st": "confirmed", "nc": 12, "nf": 0},
    {"id": "spr_a11c000000000023", "err": "ImportError: cannot import name 'LLMChain' from 'langchain'", "eco": "py", "rt": "py@3.12", "dep": ["langchain@0.2.0"], "tried": ["from langchain import LLMChain"], "fix": ("patch", "from langchain.chains.llm import LLMChain"), "eval": "true", "st": "confirmed", "nc": 5, "nf": 2},
    {"id": "spr_a11c000000000024", "err": "Named export 'foo' not found. The requested module is a CommonJS module", "eco": "npm", "rt": "node@20", "dep": ["vite@5.0.0"], "tried": ["named-esm-import"], "fix": ("patch", "import pkg from 'cjs-lib'; pkg.foo"), "eval": "true", "st": "confirmed", "nc": 8, "nf": 0},
    {"id": "spr_a11c000000000025", "err": "Error: Route \"/\" used `cookies().get`. `cookies()` was called outside a Request Scope", "eco": "npm", "rt": "node@20", "dep": ["next@15.0.0"], "tried": ["cookies-sync"], "fix": ("patch", "const jar = await cookies()"), "eval": "true", "st": "confirmed", "nc": 9, "nf": 0},
    {"id": "spr_a11c000000000026", "err": "OSError: You are trying to access a gated repo", "eco": "py", "rt": "py@3.12", "dep": ["huggingface_hub"], "tried": ["from_pretrained"], "fix": ("constraint", "huggingface-cli login, then accept the model terms on the hub"), "eval": "true", "st": "confirmed", "nc": 4, "nf": 0},
]


def materialize() -> list[Claim]:
    out: list[Claim] = []
    for raw in _SEEDS:
        err = raw["err"]
        cls = classify(err)
        dep = list(raw.get("dep") or [])
        eco = raw.get("eco") or "other"
        rt = raw.get("rt") or ""
        k, b = raw["fix"]
        out.append(Claim(
            id=raw["id"],
            fp=fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep),
            cls=cls, err=normalize_error(err), eco=eco, rt=rt, dep=dep,
            tried=list(raw.get("tried") or []),
            fix=Fix(k=k, b=b), eval=EvalSpec(cmd=raw["eval"]),
            st=raw.get("st") or "proposed", nc=int(raw.get("nc") or 0), nf=int(raw.get("nf") or 0),
            own="did:claimidx:seed", ts=_TS, note=raw.get("note") or "", src="seed",
        ))
    return out
