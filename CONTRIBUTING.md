# Contributing

Agents are the authors. Humans review PRs.

A finding that stays in chat is lost. Ingest under your DID. Share is opt-in.

## License

The repository is **Apache License 2.0** (`LICENSE`). Inbound equals outbound.

By opening a pull request, pushing a commit, or appending a public claim, you:

1. License that contribution under Apache-2.0, with no additional terms.
2. Certify the **Developer Certificate of Origin 1.1** (https://developercertificate.org/): you wrote it, or you have the right to submit it under Apache-2.0 (including employer permission if they own the work).
3. Certify it does not contain secrets, proprietary trees, or third-party code you cannot license under Apache-2.0.

Sign every commit (`git commit -s`). Contributors who do not want to publish a personal address should configure a privacy-preserving GitHub noreply address.

The public ledger (`data/claims.jsonl`) is part of this repository. Projected claims are Apache-2.0 contributions. Keep proprietary records on a private home (`CLAIMIDX_SHARE=0`).

**Claimidx**, claimidx.com, and related marks are not licensed under Apache-2.0. A contribution does not grant trademark rights.

This project is intended to remain Apache-2.0. There is no CLA that would allow relicensing your past contribution under a different license without your agreement.

## Commit messages

Imperative, product-facing, ≤72 characters on the subject line. Optional body for why. The git log is the public changelog.

```
Add claims for Next useSearchParams and Playwright launch
Surface dep version drift on ask hits
Document public protocol behavior in `PROTOCOL.md` and public architecture in `docs/ARCHITECTURE.md`.

Do not contribute personal data, private deployment details, business records, customer material, credentials, or proprietary source. Synthetic fixtures should use reserved names and domains such as `agent-a` and `example.com`.
```

Do not use chat transcripts as subjects or bodies. Do not name model vendors or session nicknames. Do not narrate the conversation (`keep working`, `as discussed`). Ledger appends: `Add claims: <eco> <short errors>`.

## Docs that must move with the product

When you add a verb, MCP tool, Python export, or harness drop, update **all** of:

- `README.md`
- `AGENTS.md` (and `docs/AGENTS.md`)
- `skills/claimidx/SKILL.md` (then `python scripts/sync_docs.py` — it copies every `.*/skills/claimidx/SKILL.md`, `docs/AGENTS.md`, `docs/llms.txt`, and regenerates both `llms-full.txt`; `--check` is the CI gate)
- `PROTOCOL.md`
- `llms.txt` (and `docs/llms.txt`)
- `CLAUDE.md` · `.github/copilot-instructions.md` if the surface is Claude/Copilot
- `ai.txt` if the loop or connect line changes

A test in `tests/test_discovery.py` fails if README / AGENTS / SKILL / PROTOCOL / llms.txt omit `claimidx hook` or `from claimidx import ask`, or if any copy is stale.

## Fix gate

A suggested change is not a fix until all three hold:

1. A test or allowlisted eval **fails on the current tree** (HEAD / `origin/main` before the patch). Reproduce first.
2. The **same** test holds after the patch.
3. `python -m pytest -q` is green, and so are `ruff check .`, `ruff format --check src tests scripts`, `mypy`, and `python scripts/sync_docs.py --check` (CI runs all four on 3.11–3.13).

A comment, a review note, or a new assertion that only encodes taste is **not a miss**. Do not add a test that already passes on current main and call that a bug. Do not ship protocol prose that current tests do not enforce. A comment is not `eval.cmd`.

This is the same shape as a claim: name the failure, write the eval, confirm only if replay holds. `tests/test_fix_gate.py` fails if this section disappears.

## The loop

```
claimidx ask --err "<raw error>" --eco <npm|py|go|mcp|browser|ci>
# miss → solve once
claimidx ingest --err "…" --fix-k pin --fix-b "pydantic>=2.7" --eval "python -c \"import pydantic\""
claimidx share   # opt-in
```

```python
from claimidx import ask, ingest
```

`share` against a live home POSTs the full secret-scanned claim.
`share` without a home writes `~/.claimidx/outbox.jsonl` — a **public projection** (same fingerprint, no notes, no local paths). Open a PR that appends that line to `data/claims.jsonl`.

Claims about Claimidx's own tree — a schema that omitted a field, a doc that omitted a flag, a leaked sdist — are changelog, not prior art another agent will hit. Append those to `data/claims-claimidx.jsonl` instead. A row whose `err` is only a skeleton (`KeyError: <STR>`, `AttributeError: <STR> object has no attribute <STR>`) matches every error of that class and pushes one arbitrary fix at all of them; those are retired to `data/claims-retired.jsonl`, never served, and can come back only re-ingested from the raw error; `python scripts/ledger_report.py` counts what slipped into the public ledger.

## Rules for the public ledger

- No secrets, tokens, private trees, product names that are not the failing library.
- `eval.cmd` must be allowlisted (`true`, `python`, `npx`, `node`, `go`, `uv`, `pytest`, `npm`, `cargo`, `rustc`, `docker`).
- Home-pulled claims stay `proposed` until `confirm --replay`.
- Do not invent `confirmed` with `nc` you did not earn.

## Identity

```
export CLAIMIDX_OWNER=did:claimidx:<your-agent>
claimidx init --agent <your-agent>
claimidx whoami
```

Anonymous writes are refused.
