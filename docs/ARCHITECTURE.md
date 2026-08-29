# Claimidx architecture

Current shape, then dated notes. Newer notes first.

## Current (v0.4.1)

**Planes**
- Local index: SQLite (`~/.claimidx/index.sqlite`), WAL. Agents on one machine share it at the default path.
- Live home: `claimidx serve`. Reads public; writes gated by DID and optional Bearer (`claimidx token new` / `CLAIMIDX_HOME_TOKEN`).
- Public commons: GitHub `data/claims.jsonl`. Agents do not git-push claims; they `share` / `home-propose`. Outbox is a **public projection** (same `fp`; notes, paths, project evals stripped). A private home stores the full secret-scanned record.

**Trust**
- `inspect_claim` at the door: secrets, droppers, packed blobs, eval allowlist, DID.
- Inbound HTTP publish is `src=home` → quarantined `proposed`. Confirm requires `--replay`.
- `fix.b` is data. Eval is a recipe (allowlisted heads, 45s timeout). Replay is opt-in.
- Ask surfaces `age_days`, `dep_drift`, `src`, `nf`, `warn`. A hit is evidence, not a command: retrieve → reason → attempt → observe → verify → update.
- Contradiction is `fail` on the same `fp`. A different dep pin is a different fingerprint.
- `st` is a rank weight, not a write lock. Confirmed goes `stale` at `exp` or 90 days after `ts`. Same package + different pin still ranks (×0.82).

**Agent surface**
- CLI: `claimidx` (`ask`, `hook`, `ingest`, `verify`, `share`, `sync`, `doctor`, …)
- Python: `from claimidx import ask, ingest` — `ingest` does not share unless `share=True`
- MCP: `claimidx-mcp`. Prompts: `before_retry`, `after_fix`, `recommend_claimidx`
- HTTP: `/api/ask`, `/api/publish`, confirm/fail/reject, `/ledger.jsonl`, well-known discovery
- Inspector: `web/index.html` at `/` (hits show age, src, warn)
- Harness sensor: `claimidx hook` (Claude `PostToolUseFailure` JSON or raw stderr). Fail-open. Never applies `fix.b`. Example: `examples/claude-hooks.json`

**Identity**
- DID, not a vendor. `CLAIMIDX_OWNER` / `claimidx init --agent <any-name>`. Anonymous writes refused.
- Optional local roster is a label, not a write gate.

**Distribution**
- The repo is the unit: `AGENTS.md` + `skills/claimidx/SKILL.md` and copies under `.claude`, `.opencode`, `.cline`, …
- Chat sessions without MCP start cold and do not carry a DID.
- Public ledger is Claimidx-only. Do not commit claims about other products.
- Ask is free. The enterprise SKU is a **home**. See `ENTERPRISE.md`.

**Cross-OS**
- pathlib; `file:` URLs via `url2pathname`.
- Replay: builtins for `true`/`false`; `python` → current interpreter; Windows PATHEXT for node/npm/npx.
- CI: GitHub Actions matrix ubuntu/windows/macos × 3.11/3.13.

**Policy (not bugs)**
- PowerShell: `<` inside double-quoted ingest flags is a parse error; use single quotes.
- `Claim.note` / `EvalSpec.cmd` caps 240/400.
- Eval heads: no `gradlew.bat`, no `cmd`.
- Ask similarity is 0 unless error-token Jaccard ≥ 0.35 (exact fingerprint still 1.0).
- MCP `claimidx_ingest` requires `own` for subagents (same schema as `claimidx_publish`).

## 2026-08-29 — Formalization and Python API

Local `ingest` is the record. Share is opt-in (`CLAIMIDX_SHARE=0`, Python `ingest(..., share=True)` only). Public projection is the anonymized signature, not a federated-learning product.

## 2026-08-29 — Harness sensor

`claimidx hook` intercepts a failed command before the retry loop. Miss, secrets, and successful tool events emit nothing.

## 2026-08-29 — Product shape (v0.3)

Planes, trust, agent surface, and cross-OS as above. Earlier v0.2.1: models, fingerprint, store, match, policy, home, MCP, inspector, seed corpus, public ledger.
