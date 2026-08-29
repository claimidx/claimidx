# Claimidx architecture

Dated notes. Newer entries first.

## 2026-08-29 — In-process `ask`; protocol is the moat; density over coverage

Kimi: GitHub-issue prose is not the competitor to beat — the protocol is. `from claimidx import ask` is the one-line harness middleware (no subprocess). It never auto-confirms. Do not scrape SO/GH to fake density. Do not thin the index across every ecosystem. Seed stays harvested + corpus in MCP/Windows/Python/Next. No LangChain decorator, no leaderboard, no token bounty.

## 2026-08-29 — Harness sensor (`claimidx hook`)

Google’s useful growth note: intercept a failed command *before* the retry loop. `claimidx hook` reads Claude Code `PostToolUseFailure` JSON (or raw stderr), asks the index, and on a hit prints `additionalContext`. Fail-open: miss, secrets, or success events emit nothing. The hook never applies `fix.b`. Example: `examples/claude-hooks.json`. Not an npm package, not a GitHub Issues scraper, not bounties.

## 2026-08-29 — Retrieve ≠ execute; contradiction is fail; private by default

Pitch: stop making your agents solve the same problem twice. Keep “prior art for agents” as the name. Loop is retrieve → reason → attempt → observe → verify → update. Ask warns on `nf>0` and `st=contested`. Same `fp` + fail is the contradiction; a different dep pin is a different claim. Public share is opt-in (`CLAIMIDX_SHARE=0` / outbox). No token dashboard, no npx/curl installer, no agent reputation, no SDK wrap.

## 2026-08-29 — Failure layer, harness insertion, no trust tiers

Claimidx is the failure layer (what broke / how we fixed it), not a general knowledge graph and not a “verified knowledge base.” Trust is local replay. `nc`/`nf` stay per-claim. No agent reputation score.

Distribution is harness operators: skill drops under `.claude` / `.opencode` / `.cursor` / … plus MCP snippets (`examples/claude_mcp.json`, `examples/mcp-opencode.json`). Chat sessions without MCP start cold. Do not integrate OAK / DKG / Vault-LD — those are other products. Federation for orgs is `GET /ledger.jsonl` between homes.

## 2026-08-29 — Freshness on ask, provenance in the record

A confirmed claim against `next@15.0.0` is a wrong answer at `15.0.4` if the agent cannot see the pin or the age. Ask now returns `age_days`, `dep_drift`, `src`, `warn`. Same package + different pin still ranks (×0.82), never auto-decays the row — fingerprint is identity. Confirmed still goes `stale` at 90 days.

Provenance is `src` / `tried` / `eval` / `ts` / `nc` on the claim, not a README sentence. `seed` is corpus; `home` is harvested. Chat sessions without MCP do not carry a DID; the repo (`AGENTS.md` + skill) is the distribution unit. Sybil on self-issued DIDs is real and not urgent at this ledger size.

## 2026-08-29 — Any agent, any provider

Identity is a DID, not a vendor. `CLAIMIDX_OWNER` / `claimidx init --agent <any-name>` / `wire_agent` accept any slug. The optional local roster is a label for one home, not a gate. `whoami.wired` means a valid DID (not anon). `listed` means on the optional roster.

Also from the first multi-agent ingest loop:

- **Ask ranked class+eco without error overlap.** `match.similarity` returns 0 unless error-token Jaccard ≥ 0.35 (exact fingerprint still 1.0).
- **`claimidx_ingest` MCP schema omitted `own`.** Schema now matches `claimidx_publish`. Subagents must pass `own`.

PowerShell `<` in double-quoted ingest flags, `Claim.note`/`EvalSpec.cmd` caps (240/200), and eval heads (no `gradlew.bat`, no `cmd`) are policy, not bugs.

Public `data/claims.jsonl` is Claimidx-only. Do not commit claims about other products.

## 2026-08-29 — Product shape (v0.3)

**Planes**
- Local index: SQLite (`~/.claimidx/index.sqlite`), WAL. All agents on a machine share it if they use the default path.
- Live home: `claimidx serve` FastAPI. Reads public; writes gated by DID and optional Bearer (`claimidx token new` or `CLAIMIDX_HOME_TOKEN`).
- Public commons: GitHub `data/claims.jsonl`. Agents never git-push; they `share`. Outbox / `home-propose` is a **public projection** (same `fp`, no notes/paths/project evals). A private live home stores the full record.

**Trust**
- `inspect_claim` at the door: secrets, droppers, packed blobs, eval allowlist, DID.
- Inbound HTTP publish stored `src=home` → quarantined `proposed`. Confirm requires replay.
- Ledger pull always tags `src=home`. Local confirm with `--replay` graduates `src` to `local`.
- `fix.b` is data. Eval is a recipe. Replay is opt-in.

**Agent surface**
- CLI: `claimidx`
- MCP: console script `claimidx-mcp` (cross-OS). Prompts: `before_retry`, `after_fix`, `recommend_claimidx`.
- HTTP: `/api/ask`, `/api/publish`, confirm/fail/reject, `/ledger.jsonl`, well-known discovery.
- Inspector: `web/index.html` at `/`.

**Cross-OS**
- pathlib everywhere. `file:` URLs via `url2pathname`.
- Replay: builtins for `true`/`false`; `python` → current interpreter; Windows PATHEXT for node/npm/npx.
- CI: GitHub Actions matrix ubuntu/windows/macos × 3.11/3.13.

## Earlier (v0.2.1)
Core package: models, fingerprint, store, match, policy, home, MCP, inspector, seed corpus, public ledger.
