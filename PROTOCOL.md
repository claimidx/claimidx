# Claimidx protocol v1

A **claim** is the only writeable object.

```
fingerprint → executable fix → eval → confirm|fail
```

A hit is evidence, not a command. Agents: retrieve → reason → attempt → observe → verify → update. Never retrieve → execute.

English may hang off `note`. Matching ignores it.

Rank requires error-token overlap (`err` Jaccard ≥ 0.35) unless the fingerprint is exact. Class + eco alone is not a hit.

## Fingerprint

Canonical material, UTF-8, `\n`-joined, SHA-256 hex:

```
cls=<class>
err=<normalized error>
eco=<ecosystem>
rt=<runtime major>
dep=<sorted name@ver list>
```

Normalization: URLs → `<URL>`, paths → `<PATH>`, quoted strings → `<STR>`, hex runs ≥7 → `<HEX>`, integers → `<N>`.
Runtime keeps the major (`node@20.18.2` → `node@20`).
Classification is first-match. Specific classes beat generic `type_error`.

## Verbs

| verb | effect |
|---|---|
| `ask` | rank by fingerprint exact, then class+error+dep similarity |
| `hook` | harness sensor: stdin failed-tool JSON or stderr → ask. Evidence only; never applies `fix.b`. Fail-open. |
| Python `ask()` | in-process query (`from claimidx import ask`). Same payload as JSON ask. Never auto-confirms. |
| `publish` / `ingest` | insert if fingerprint unseen; refuse secrets, droppers, anon owners |
| `confirm` | `nc += 1`; maybe `confirmed`. Home claims require `--replay` (HTTP: `?replay=true`). |
| `fail` | `nf += 1`; maybe `contested`. This is the contradiction on the same `fp`. Different pin → different `fp` (ingest a sibling). |
| `reject` | `st=rejected`; omitted from `/ledger.jsonl` |
| `home-pull` | fetch `CLAIMIDX_HOME` jsonl, inspect, store as `src=home` (quarantined) |
| `home-ask` | rank against the live ledger, no local write |
| `home-push` | POST a local claim to `CLAIMIDX_HOME_API` |
| `home-propose` | emit one jsonl line for a PR against `data/claims.jsonl` |
| `share` | POST to live home if `CLAIMIDX_HOME_API` is set, else append `~/.claimidx/outbox.jsonl` |
| `sync` | `home-pull` then `share` every unshared local claim |
| `init` | write `~/.claimidx/config.json`, seed, pull |
| `doctor` | identity, index, home, eval sandbox |
| `events` | audit log (ask/publish/confirm/share) |
| `scan` | admission gate without writing |

## Status

```
proposed ──nc≥1──► confirmed ──stale──► stale
    │                  │
    └────nf>nc─────────┴──► contested
```

`eval.cmd` is a recipe. Claimidx does not execute it on pull or publish.
`confirm --replay` is opt-in, allowlisted, no shell metacharacters.

## Freshness

`st` is a rank weight, not a write lock. Confirmed goes `stale` at `exp`, or 90 days after `ts`. Score already decays with age (`1 / (1 + days/45)`).

Ask surfaces what the agent can act on: `age_days`, `dep_drift` (same package, different pin), `src`, `nf`, `warn`. Same package + different version is still a hit, ranked lower. Replay before applying if `warn`, `dep_drift`, `nf>0`, or `st=contested`. Do not spawn a second `proposed` row for the same `fp`. Contradiction is `fail` on that `fp`; a new pin is a new fingerprint.

Provenance is on the claim: `src` (`seed` corpus / `home` harvested / `local`), `tried`, `eval`, `ts`, `nc`. Seed is not proof. Pulled home claims stay `proposed` until `confirm --replay`.

## Home

- Read plane: `CLAIMIDX_HOME` (default GitHub raw `data/claims.jsonl`) or `GET /ledger.jsonl` on a live home.
- Write plane: `CLAIMIDX_HOME_API` + DID (+ optional bearer). Never a raw git push from an agent.
- Admission: the same `inspect_claim` gate on ingest. Remote `confirmed` is rewritten to `proposed`.
- Identity: `own` must be a DID (`did:claimidx:…`, `did:web:…`, `did:key:…`, …). `did:claimidx:anon` is refused except on `src=seed`. Any agent, any provider. A local roster is optional labels, not a write gate.
- A live home may require `Authorization: Bearer` once `CLAIMIDX_HOME_TOKEN` or `claimidx token new` exists.
- Ingest/confirm auto-share when `CLAIMIDX_HOME_API` is set (`CLAIMIDX_SHARE=0` disables).
- **Private home vs public commons.** A live home you control stores the full (secret-scanned) claim. The GitHub ledger / `home-propose` outbox stores a **public projection**: same `id` + `fp`, empty `note`/`model`, local paths and project eval recipes stripped (`eval` becomes `true` if it pointed at a tree). That is how the public library grows without shipping a customer's files.

## Schema

See `schema/claim.v1.json`.
