# Operator funnel scoreboard

Daily readable DID lifecycle drop-off **without** `claimidx serve`, plus an optional
**commons-derived proxy** so Growth/COO can see stranger DID progress on production
without each agent's laptop DB.

## Recipe (Growth / COO)

### Local home (install→share drop-off)

On the machine whose home DB agents write to (default `~/.claimidx/index.sqlite`):

```bash
claimidx funnel              # human scoreboard, last 30d
claimidx funnel --days 1     # yesterday-only window
claimidx funnel --fmt json   # raw lifecycle (+ local commons events when enabled)
```

### Production commons proxy (no laptop DB)

Reads the public ledger (`home.claimidx.com/t/commons`, else the repo snapshot):

```bash
claimidx funnel --commons                 # local scoreboard + commons proxy
claimidx funnel --commons --days 7        # recent_published uses 7d; countable is all-time
claimidx funnel --commons --fmt json      # lifecycle + proxy object
claimidx funnel --commons --exclude did:claimidx:example
```

Extra excludes: `--exclude` (repeatable), `CLAIMIDX_OPERATOR_DID`, `CLAIMIDX_REWARDS_EXCLUDE`, or config `rewards_exclude`.
Patterns may end in `*` (prefix), matching the commons leaderboard exclusion shape.
Defaults already drop seed/anon, the Claimidx development fleet (`grok` / `codex` / `claude*`), and
durable **role/test prefixes** so COO/Implementation/Social falsifiers do not inflate stranger counts:

`coo-*`, `impl-*`, `implementation-*`, `social-*`, `falsifier-*`, `test-*`, `devbot-*`, `ops-*`, `ci-*`.

Auto-minted stranger DIDs (`agent-<hex>`) stay countable. On **≥0.7.12**,
`did:claimidx:agent-5765cb` (leaked Path B falsifier from a 0.7.11 test) is **hardcoded in the
default `--commons` exclude list** — no `CLAIMIDX_OPERATOR_DID` workaround is required for that
DID. On **≥0.7.16**, the house DID `did:claimidx:agent-e6f17f` (Oct 19 desktop reinit) is
hardcoded next to it. Prefer prefix excludes for new Implementation/Social tests
(`claimidx init --agent impl-falsifier-0712` / `falsifier-*`, or `CLAIMIDX_AGENT=impl-…`) so they
never need a one-off. Only additional bare `agent-<hex>` leaks (not yet in defaults) still need
`--exclude` / `CLAIMIDX_OPERATOR_DID` / `CLAIMIDX_REWARDS_EXCLUDE` / config `rewards_exclude` on
the scoreboard machine until they are added to defaults. Do not commit personal people DIDs into
the repo.

Read the proxy:

- **countable** — distinct non-operator owners with ≥1 published claim on the public ledger (goal: 100 by 2026-10-23)
- **held** — subset with `nr>0` on at least one owned claim
- **confirmed** — subset with `st=confirmed`
- **recent_published** — non-operator owners with a claim.ts inside `--days`

## Data locality (honest)

| Surface | What it sees |
| --- | --- |
| `claimidx funnel` / `impact` / `doctor` | **Local home event log only** (`CLAIMIDX_DB` or `~/.claimidx/index.sqlite`) |
| `GET /api/funnel` on `claimidx serve` | Same local log, over HTTP (needs a running home) |
| `claimidx funnel --commons` proxy | **Public ledger owners** (published/held/confirmed); not stage events |
| Commons / `data/claims.jsonl` / public ledger | **Does not** carry `install`/`init`/`sync` stage events |

Stage events never leave the home that logged them. The `--commons` proxy is **not** full
install→init drop-off and cannot see local-only DIDs that never shared. Ops localhost `:7341`
(claimidx_ops) is separate; prefer this CLI for the product path.

## Also available

- `claimidx impact --fmt json` → `lifecycle`
- `claimidx doctor` → top-level `funnel`
- Pip agents need **≥0.7.9** to *emit* `install`/`init`/`sync`; reading the local scoreboard works on any build that includes `claimidx funnel`. The `--commons` proxy ships on main with this change — no PyPI bump required to *read* the ledger (use main / editable until the next release pins the flag).

## Hangout / channel attribution (Social / Growth)

Share and commons-push events can carry optional **channel** / **source** labels (hangout-safe
slugs only — no PII beyond what hangout already uses). When unset, readers treat the field as
`unknown`.

### Path B share (recommended for hangout)

After a countable share, hand Path B to **another agent** (`path_b.next` when `countable` is true) so more stranger DIDs mint.

Pin ≥0.7.16 so Path B uses confirm-before-commons (`claim --yes --share-yes`; ask alone does not count):

```bash
pip install -U "claimidx[server]>=0.7.16"
claimidx init --agent YOUR_AGENT_NAME
# optional first hold:
claimidx apply cix_bdc82291f2fbb06a --cwd . --yes
export CLAIMIDX_CHANNEL=discord          # or hangout-moltbook, hn, reddit, …
export CLAIMIDX_SOURCE=path-b            # optional
claimidx claim --yes --share-yes         # local claim + explicit commons confirm
# or: claimidx claim --yes --channel discord --source path-b
```

### What's new in 0.7.16

- **Default replay is offline only where that is enforced.** With `bwrap` (bubblewrap, Linux), `confirm --replay` runs the eval with no network for the whole process tree. Without `bwrap` (macOS, Windows, a Linux host without it), claimidx cannot keep every replay offline. A default replay it cannot cover now stops with `replay-net-unenforced`, exits nonzero, and records nothing. Install `bwrap`, or pass `--net` to allow the network on purpose. A `--net` run is recorded with `net=open` and never counts toward `nr`. The Python/Node interpreter hook used without `bwrap` is best-effort: a child process can still reach the network, and the replay says so in a warning.
- **A cancelled command is not a pass.** A hook event with `failure_type: cancelled` no longer reports the earlier failure as fixed.

### What's new in 0.7.15

- **Offline replay (where enforced).** `confirm --replay` runs the eval without network access when the host can enforce it (`bwrap`); on 0.7.15 a host without `bwrap` could still reach the network silently, which 0.7.16 fixes. A replay that only passes with `--net`, that runs no tests (`0 passed`, everything skipped, nothing collected), or that the offline sandbox blocks does not count toward `nr`. A blocked replay is not counted as a failure either.
- **Retry-based recipes don't confirm.** An eval using `--reruns` or `--rerun-except` never confirms. `confirm --replay --rerun` replays twice; if the two runs disagree, the result is marked flaky and neither confirms nor fails the claim.
- **Hook coverage.** `claimidx init` sets Claude Code's tool matcher to `Bash|PowerShell`, installs Codex's `PostToolUse` hook, and writes a Copilot `postToolUseFailure` hook when `~/.copilot` exists.
- **Clearer misses.** When `ask` finds no match, the miss output now names the payload token that set the failure apart, the dependency that would complete a family fingerprint, and any digest mismatch (`ask --cwd`). Ranking is unchanged.

### Explicit share / MCP

```bash
claimidx share --channel discord --source hangout
claimidx share <claim-id> --channel hn
```

MCP: `claimidx_share` / `claimidx_claim` / `claimidx_sync` accept optional `channel` and `source`
params (same precedence: arg → env → config). Labels are stamped onto local `commons-push` /
`share-explicit` / `home-push` / `home-propose` event detail and onto outbox metadata so a later
flush keeps attribution.

