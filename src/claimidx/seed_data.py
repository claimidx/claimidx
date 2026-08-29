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
