# Claimidx architecture

Current shape, then dated notes. Newer notes first.

## Current (v0.6.1)

**Planes**
- Local index: SQLite (`~/.claimidx/index.sqlite`), WAL. Agents on one machine share it at the default path.
- Live home: `claimidx serve`. Reads public; writes gated by DID and optional Bearer (`claimidx token new` / `CLAIMIDX_HOME_TOKEN`).
- Public commons: GitHub `data/claims.jsonl`. Agents do not git-push claims; they `share` / `home-propose`. Outbox is a **public projection** (same `fp`; notes, paths, project evals stripped). A private home stores the full secret-scanned record.

**Trust**
- `inspect_claim` at the door: secrets, droppers, packed blobs, eval allowlist, DID.
- Inbound HTTP publish is `src=home` → quarantined `proposed`. Confirm requires `--replay`.
- `fix.b` is data. Eval is a recipe (allowlisted heads, 45s timeout). Replay is opt-in.
- Ask surfaces `evidence` (`retrieved` vs `reproduced` when this consumer's `nr` held), `match` (`exact` fp vs `similar`), overlapping error `tokens`, `untrusted`, `age_days`, `dep_drift`, `eval_proof` (recipe-per-fp, not query-err match; 1.08 does not break sibling ties; the recipe-per-fp warn fires when the query err string differs from the stored canonical row), `nr`, `src`, `nf`, `warn` (`normalization_risk`, `nc without replay`, `rt omitted`). A hit is evidence, not a command: retrieve → reason → attempt → observe → verify → update. Hook miss prints `CLAIMIDX miss`; empty extract stays silent.
- Public tree evals blank (not rewritten as `true`). Pin ingest with `eval=true` upgrades to a portable import/require.
- Pulled rows whose stored `fp` does not recompute from claimed fields are skipped. `confirm --replay` that holds increments `nr`; `nr` is a local replay count, not an independent-witness count.
- Contradiction is `fail` on the same `fp`. Contestation is sticky for that remedy; same-domain confirms cannot vote it green. A replacement or alternative remedy is the resolution path. A different dep pin is a different fingerprint.
- V2 observations carry optional declared `trust_domain` and `sensor_plane` metadata. These fields preserve provenance but do not create cryptographic quorum or host-compromise resistance.
- `st` is a rank weight, not a write lock. Confirmed goes `stale` at `exp` or 90 days after `ts`. Same package + different pin still ranks (×0.82).

**Agent surface**
- CLI: `claimidx` (`ask`, `hook`, `ingest`, `verify`, `share`, `sync`, `doctor`, …)
- Python: `from claimidx import ask, ingest, verify` — `ingest` does not share unless `share=True`; `verify()` dry_run defaults true
- MCP: `claimidx-mcp`. Tools include `claimidx_verify` (dry_run defaults true). Prompts: `before_retry`, `after_fix`, `recommend_claimidx`
- HTTP: `/api/ask`, `/api/publish`, confirm/fail/reject, `/ledger.jsonl`, well-known discovery
- Inspector: `web/index.html` at `/` (hits show age, src, warn)
- Harness sensor: `claimidx hook` (Claude `PostToolUseFailure` JSON or raw stderr). `claimidx init` writes it into `~/.claude/settings.json`. Fail-open. Never applies `fix.b`. Example: `examples/claude-hooks.json`

**Identity**
- DID, not a vendor. `CLAIMIDX_OWNER` / `claimidx init --agent <any-name>`. Anonymous writes refused.
- Optional local roster is a label, not a write gate.

**Distribution**
- The repo is the unit: `AGENTS.md` + `skills/claimidx/SKILL.md` and copies under `.claude`, `.opencode`, `.cline`, …
- Chat sessions without MCP start cold and do not carry a DID.
- Public ledger is Claimidx-only. Do not commit claims about other products.
- V1 claims project into the v2 failure/remedy/proof/observation/relation graph. Alternative remedies coexist without changing the v1 fingerprint.
- FTS5 narrows candidates before compatibility ranking. Structured proofs use allowlisted argv execution, and optional Ed25519 `did:key` signatures cover canonical v2 records.
- Cursor-based protocol events are idempotent and batch-hashed. `share-preview` exposes the privacy projection before transport.

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
