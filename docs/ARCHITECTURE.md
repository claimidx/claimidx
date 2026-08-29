# Claimidx architecture

Dated notes. Newer entries first.

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
