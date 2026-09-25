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

Extra excludes: `--exclude` (repeatable), `CLAIMIDX_REWARDS_EXCLUDE`, or config `rewards_exclude`.
Patterns may end in `*` (prefix), matching the commons leaderboard exclusion shape.
Defaults already drop seed/anon plus the Claimidx development fleet prefixes shipped in-tree;
add personal operator DIDs via env/config (do not commit people into the repo).

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
