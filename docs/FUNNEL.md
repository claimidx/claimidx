# Operator funnel scoreboard

Daily readable DID lifecycle drop-off **without** `claimidx serve`.

## Recipe (Growth / COO)

On the machine whose home DB agents write to (default `~/.claimidx/index.sqlite`):

```bash
claimidx funnel              # human scoreboard, last 30d
claimidx funnel --days 1     # yesterday-only window
claimidx funnel --fmt json   # raw lifecycle (+ commons when enabled)
```

Read:

- **countable DIDs** — distinct non-seed/non-anon actors that reached any stage
- **stages** — `install → init → ask → sync → confirm → publish → share` (actors / events)
- **COO path line** — shorthand `install → init → ask → hold → claim` where `confirm≈hold`, `publish≈claim`
- **drop-off** — actors whose *maximum* stage is that step (e.g. `init_no_ask`)

## Data locality (honest)

| Surface | What it sees |
| --- | --- |
| `claimidx funnel` / `impact` / `doctor` | **Local home event log only** (`CLAIMIDX_DB` or `~/.claimidx/index.sqlite`) |
| `GET /api/funnel` on `claimidx serve` | Same local log, over HTTP (needs a running home) |
| Commons / `data/claims.jsonl` / public ledger | **Does not** carry `install`/`init`/`sync` stage events |

Stage events never leave the home that logged them. There is no commons rollup of stranger funnels. Ops localhost `:7341` (claimidx_ops) is separate; prefer this CLI for the product path.

## Also available

- `claimidx impact --fmt json` → `lifecycle`
- `claimidx doctor` → top-level `funnel`
- Pip agents need **≥0.7.9** to *emit* `install`/`init`/`sync`; reading the scoreboard works on any build that includes `claimidx funnel` (this PR). No PyPI bump required just to read existing events.
