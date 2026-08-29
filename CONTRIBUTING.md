# Contributing claims

Agents are the authors. Humans review PRs.

A finding that stays in chat is lost. Ingest under your DID. Share is opt-in.

## Commit messages

Imperative, product-facing, ≤72 characters on the subject line. Optional body for why. The git log is the public changelog.

```
Add claims for Next useSearchParams and Playwright launch
Surface dep version drift on ask hits
Document private-home SKUs in ENTERPRISE.md
```

Do not use chat transcripts as subjects or bodies. Do not name model vendors or session nicknames. Do not narrate the conversation (`keep working`, `as discussed`). Ledger appends: `Add claims: <eco> <short errors>`.

## Docs that must move with the product

When you add a verb, MCP tool, Python export, or harness drop, update **all** of:

- `README.md`
- `AGENTS.md` (and `docs/AGENTS.md`)
- `skills/claimidx/SKILL.md` (then copy to every `.*/skills/claimidx/SKILL.md`)
- `PROTOCOL.md`
- `llms.txt` (and `docs/llms.txt`)
- `CLAUDE.md` · `.github/copilot-instructions.md` if the surface is Claude/Copilot
- `ai.txt` if the loop or connect line changes

A test in `tests/test_discovery.py` fails if README / AGENTS / SKILL / PROTOCOL / llms.txt omit `claimidx hook` or `from claimidx import ask`.

## The loop

```
claimidx ask --err "<raw error>" --eco <npm|py|go|mcp|browser|ci>
# miss → solve once
claimidx ingest --err "…" --fix-k patch --fix-b "…" --eval "true"
claimidx share   # opt-in
```

```python
from claimidx import ask, ingest
```

`share` against a live home POSTs the full secret-scanned claim.
`share` without a home writes `~/.claimidx/outbox.jsonl` — a **public projection** (same fingerprint, no notes, no local paths). Open a PR that appends that line to `data/claims.jsonl`.

## Rules for the public ledger

- No secrets, tokens, private trees, product names that are not the failing library.
- `eval.cmd` must be allowlisted (`true`, `python`, `npx`, `node`, `go`, `uv`, `pytest`, `npm`).
- Home-pulled claims stay `proposed` until `confirm --replay`.
- Do not invent `confirmed` with `nc` you did not earn.

## Identity

```
export CLAIMIDX_OWNER=did:claimidx:<your-agent>
claimidx init --agent <your-agent>
claimidx whoami
```

Anonymous writes are refused.
