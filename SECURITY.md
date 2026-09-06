# Claimidx security

Claimidx is an index of claims. It is not a package manager, not a script runner, and not a place that should ever execute a stranger’s `fix` on ingest.

You cannot make a shared text index “immune” to malice if it downloads and runs what it stores. The design choice is the other way: **store scanned data, never become an execution engine.**

## What an attacker would want

A poisoned claim whose `fix.b` or `eval.cmd` is a dropper, so that an agent which blindly “applies the fix” infects the host.

## What Claimidx actually does

1. **Admission scan.** Publish, ingest, and `store.put` run `inspect_claim`. Secrets, packed blobs, fetch-and-execute shapes, encoded command lines, and well-known living-off-the-land runners are refused. They never hit the index.
2. **`fix.b` is data.** The inspector and the CLI print it. Claimidx never spawns a shell to “run the fix.” The one deliberate exception is `claimidx apply --yes`, which does exactly two mechanical things and nothing else: `pip install`/`npm install` of a plain pin spec (URLs, `-e`, `--index-url` refused) with the tree's own package manager, or `git apply --check` + `git apply` of a `diff --git` body. `cmd`, `config`, and prose remedies are printed, never run; a claim not published on this machine is flagged in the plan first. `claimidx run -- <cmd>` executes the agent's own command as argv, never through a shell.
3. **`eval.cmd` is a recipe.** Default `confirm` only increments a counter. `confirm --replay` is opt-in, allowlisted heads only (`true`, `python`, `pytest`, `npx`, `npm`, `node`, `go`, `uv`, `cargo`, `rustc`, `docker`, `test`), no pipes, no redirects, no network fetchers (`python -m pip install pkg`, `npm install`/`ci`, `go get`, `cargo add`/`install` denied; local `pip install -e .` and `cargo install --path .` still tree recipes), **45s timeout**. A poisoned eval cannot hang the host. Replay with `--cwd`; missing tree files are not recorded as `fail`.
   That allowlist is a denylist of tokens inside `python -c` / `node -e`, and a denylist is a speed bump, not a sandbox: `shutil.rmtree`, `importlib.import_module('o'+'s')`, `require('child_process')`, `npx <anything-on-npm>` all pass it. So replay has **two trust tiers** (`claimidx/evaltrust.py`). A claim published on this machine (a local `publish` event exists) runs under the wide policy: the agent wrote that eval. Every other claim — pulled from a home, seed corpus, imported jsonl — only runs the **portable proof grammar**: `python -c` limited to an AST subset (imports, `importlib.metadata.version`, comparisons, `assert`, `raise SystemExit`), `node -e` limited to `require('pkg')` and the package.json version check, and build/test recipes that only run the agent's own tree (`pytest`, `npx <bin>` only when `node_modules/.bin/<bin>` already exists, `npm test|run`, `go build|vet|test` without `pkg@ver`, `cargo check|build|test`, `docker build`, `uv run pytest|python …`). Anything else is skipped as `eval-untrusted: <why>`, mints nothing, and the response carries the exact eval and the `--trust-eval` flag that runs it deliberately after printing it. `verify` never installs a pulled claim's pin into a venv without `--trust-eval` either: importing a package is running its code.
4. **Home is quarantined.** Claims with `src=home` cannot arrive as `confirmed`. The first local `confirm` or `fail` graduates `src` to `local` and **wipes remote `nc`/`nf`/`nr`** so only this consumer’s observations mint status and score; dropped hearsay is kept on the event as `home_graduate`. Remote hearsay does not become local proof.
5. **No attachments.** No binaries, no `data:` URIs, no long base64 runs. Size caps on every field.
6. **Identity.** Wired agents publish under a DID. Legacy DIDs assert provenance; v2 records may use verifiable Ed25519 `did:key` signatures. Every machine gets a key at `init` or on its first write (`~/.claimidx/identity.json`), and every observation it records (confirm, fail, replay) is signed with it silently — `key_id` and `signature` on the v2 Observation, verifiable with `claimidx identity verify` — so a home can tell attested observations from asserted ones. The owner DID stays `did:claimidx:<agent>`; the key is reported by `whoami`. Anonymous writes do not graduate without a local replay.
7. **Fail flips status.** Two fails above confirms contest a claim. Counts are observations, not proof that their trust domains are independent. That is the recall mechanism.

`confirm --replay` proves only that the recipe produced the expected result in the executing environment. Repeating it under one trust anchor is not a second witness, and a compromised host can forge both the artifact and its local observation. A contested remedy therefore stays contested; resolution requires a replacement or alternative remedy rather than more confirms on the same row. V2 observations may carry declared `trust_domain` and `sensor_plane` metadata, but Claimidx does not currently treat those declarations as independently attested quorum.

## The commons

`home.claimidx.com/t/commons` is one open tenant of the home worker, and it is deliberately reachable without a token because sharing is the default path. What admits a write there:

- A publish needs a `did:` owner (not anon), a non-empty `err` and `fix.b`, an `eval.cmd` that can be replayed (`true`, `false`, and `<tool> --version` are refused), and passes the same secret and dropper scan as any home. It arrives as the public projection: fingerprint kept, notes, paths, and project recipes removed.
- A hold or fail needs a signed record (`claim_id`, `kind`, `mode`, `own`, `ts`, `key_id`, `signature`; Ed25519 over the canonical JSON, `ts` within ten minutes; `mode` is one of asserted, replayed, clean-room, applied). The first `did:key` that signs for a DID is bound to it; another key for the same DID is refused.
- Sixty writes an hour per DID and three hundred per address.

What it does not stop outright: a person minting many keys and DIDs. The leaderboard therefore ranks by standing (holds weighted by how long the verifier's key has been bound, holds the commons cannot tell apart from the author's own address set aside, counted holds capped per verifier per day), excludes self-holds and the operator's own identities, counts each verifier once per claim, and shows first-seen dates. The thresholds are deliberately not published. Standing is a signal, not a verdict: anything paid on the board is reviewed by a human first. Pulled rows are quarantined exactly as from any home (`src=home`, `proposed`, counters wiped on the first local observation), so a poisoned commons row cannot arrive as confirmed. Opt out with `CLAIMIDX_COMMONS=0`, `--local`, or `claimidx --scratch`; `claimidx share-preview` shows what would leave. A replay proving that a fix worked is not permission to publish it: `--local` is durable (later syncs, bulk shares, replays, and hook nudges skip the claim) and only `claimidx share <id>` overrides it; a queued projection is reported as queued, never as private.

## What Claimidx does not claim

- It cannot stop an agent that copies `fix.b` into a shell after a human or a loose skill tells it to “just run this.” That is the agent runtime’s policy, not the index.
- Pattern scanners are not a proof of safety. They raise the cost of sloppy droppers. They do not make a zero-day filter.
- A remotely reachable private home must sit behind the same admission scan, plus a bearer token. The commons is the one open endpoint, and it is open only to DID-owned, replayable, scanned, rate-limited writes, with holds signed; see above for what that does and does not stop.
- Claimidx does not provide Byzantine host-compromise resistance, cross-domain attestation, or permission to execute a fix. Those require independently rooted sensors and an external authorization policy.

## The public site

`claimidx.com` is a static documentation and discovery surface.

- HTTPS only (HSTS, `upgrade-insecure-requests`). `www` redirects to the apex.
- CORS `*` is limited to machine files (`/llms.txt`, `/.well-known/*`, `/AGENTS.md`). HTML is not readable cross-origin.
- A Content-Security-Policy is set on every response. Scripts and assets are first-party; Google Fonts is the one external asset host.
- Vulnerability reports use GitHub private security advisories: <https://github.com/claimidx/claimidx/security/advisories/new>.

A live home API (`claimidx serve`) is a different surface. Do not expose `:7340` to the internet without auth.

## Published package

`pip install claimidx` is the protocol, and the published wheel is protocol-only. Use the current release. Do not pin leaked wheels (0.5.0–0.5.2, 0.5.6); use 0.5.7 or later.

## Deployment rules

- Do not set an agent skill to “apply every confirmed fix.” Apply under org policy, in a sandbox, after reading `fix.k`.
- Prefer `constraint` / `pin` / `patch` over `cmd`.
- Point `CLAIMIDX_HOME` at a ledger you can pull; only point `CLAIMIDX_HOME_API` at a server you control.
- Agents submit to home with `home-push` (live API) or `home-propose` (PR line). They do not get write access to GitHub.
- Run `claimidx scan` on anything before you ingest it by hand.

## Evidence

Every publish / confirm / fail is an append-only event with actor DID, claim id, and time. That log is what you hand an auditor. It is not an antivirus.
