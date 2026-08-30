# Claimidx protocol v1

A **claim** is the only writeable object.

```
fingerprint → executable fix → eval → confirm|fail
```

A hit is evidence, not a command. Agents: retrieve → reason → attempt → observe → verify → update. Never retrieve → execute.

English may hang off `note`. Matching ignores it.

Rank requires error-token overlap (`err` Jaccard ≥ 0.35) unless the fingerprint is exact. Class + eco alone is not a hit. Jaccard drops boilerplate (`no module named`, pydantic `Input should be` / `input_value`) so payload tokens (module, field, literal) decide siblings. A short payload that is a subset of a longer dump still ranks; skeleton overlap without that payload does not.

## Fingerprint

Canonical material, UTF-8, `\n`-joined, SHA-256 hex:

```
cls=<class>
err=<normalized error>
eco=<ecosystem>
rt=<runtime major>
dep=<sorted name@ver list>
```

Normalization: URLs → `<URL>`, paths → `<PATH>`, quoted strings → `<STR>`, hex runs ≥7 → `<HEX>`, integers → `<N>`. Contractions (`Can't`) are not quotes. Public projection keeps basename evals (`python3 check.py`); it blanks tree paths, not the language suffix. A tautology `true` is a hint — projection must not manufacture one.
Runtime keeps the major (`node@20.18.2` → `node@20`).
Classification is first-match. Specific classes beat generic `type_error`.

## Verbs

| verb | effect |
|---|---|
| `ask` | rank by fingerprint exact, then class+error+dep similarity |
| `hook` (`claimidx hook`) | harness sensor: stdin failed-tool JSON or stderr → ask. `claimidx init` / `claimidx hook --install` writes Claude `PostToolUseFailure`. Evidence only; never applies `fix.b`. Fail-open. |
| Python `ask()` | in-process query (`from claimidx import ask`). Same payload as JSON ask. Never auto-confirms. |
| Python `ingest()` | in-process local write (`from claimidx import ingest`). Does not share unless `share=True`. Combined: `from claimidx import ask, ingest`. |
| `publish` / `ingest` | insert if fingerprint unseen; refuse secrets, droppers, anon owners. `--force` reuses the id, resets `nr`/`nc`/`nf` to 0, and surfaces the previous counters plus the wiped `rt` as `force_reset` on stdout/JSON. The same payload is an append-only `events` row (`kind=force_reset`, `detail={nr,nc,nf,rt}`) in the **same sqlite transaction** as the replace — a wipe event without the new row is a lie. It does not keep a hold across a rewritten `rt`. Events stay on that local index (`claimidx events` / FastAPI `GET /api/events` on `claimidx serve`). Operated cloud homes do not expose this overwrite log. Public `data/claims.jsonl` does not carry overwrite history. |
| `confirm` | `nc += 1`; maybe `confirmed`. Home claims require `--replay` (HTTP: `?replay=true`). `confirm --replay` that holds increments `nr` only when python/node evals observe the executing runtime (`ReplayResult.env`, e.g. `py@3.12`) and it matches claim.rt at proof grain (Python major.minor, Node major). Empty claim.rt cannot mint `nr` for those heads. |
| `fail` | `nf += 1`; maybe `contested`. This is the contradiction on the same `fp`. Different pin → different `fp` (ingest a sibling). |
| `verify` | batch replay. Confirm if the eval held. Fail only on a proven miss. Skip builtin `true`/`false`, missing trees, missing interpreters, and evals that cannot prove the pin. `--harness` is two-state pin replay: confirm only if unpinned misses and the pin holds. `--cwd` via a scratch dir. |
| `reject` | `st=rejected`; omitted from `/ledger.jsonl` |
| `home-pull` | fetch `CLAIMIDX_HOME` jsonl, inspect, store as `src=home` (quarantined) |
| `home-ask` | rank against the live ledger, no local write |
| `home-push` | POST a local claim to `CLAIMIDX_HOME_API` |
| `home-propose` | emit one jsonl line for a PR against `data/claims.jsonl` |
| `share` | POST to live home if `CLAIMIDX_HOME_API` is set, else append `~/.claimidx/outbox.jsonl` |
| `sync` | `home-pull` then `share` every unshared local claim |
| `init` | write `~/.claimidx/config.json`, seed, pull |
| `doctor` | identity, index, home, eval sandbox |
| `events` | audit log (ask/publish/confirm/share/force_reset). A `--force` wipe that lands is an events row in the same transaction as the replace, not only process output. Per-store sqlite; not projected to `data/claims.jsonl`. |
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

Ask surfaces what the agent can act on: `age_days`, `dep_drift` (same package, different pin), `rt_drift` (proof-grain runtime differs; fingerprint still keeps Python major only), `src`, `nf`, `nr` (replay-held confirms **for this consumer's rt**; 0 when `rt_drift` or query rt omitted against a keyed claim — no same-major fallback), `eval_proof` (recipe-shaped eval on that claim's fp, not a match against the query error; the 1.08 weight lifts every recipe sibling equally and does not adjudicate ties; ask warns `eval_proof is recipe-per-fp, not query-err match` when the query err string differs from the stored canonical row — same `normalize_error` form is not exact; quoted non-module tokens collapse to `<STR>`), `warn`. Same package + different version is still a hit, ranked lower. Replay before applying if `warn`, `dep_drift`, `rt_drift`, `nf>0`, `normalization_risk`, `nc without replay`, or `st=contested`. `claimidx hook` injects two hits when top-2 sim is a near-tie; it still does not apply `fix.b`. `normalization_risk` is content-based: it fires when the query has a path, URL, integer, hex, or non-module quoted token that `normalize_error` erases, or already contains those placeholders (`<STR>`, `<URL>`, `<PATH>`, `<HEX>`, `<N>`). Counter-only `confirm` still increments `nc`; `confirm --replay` that holds increments `nr` only when python/node env matches claim.rt. Do not spawn a second `proposed` row for the same `fp`. `--force` overwrites that row and resets `nr`/`nc`/`nf` (JSON `force_reset` carries the previous counters and the wiped `rt`; the same payload is `events.kind=force_reset` in the same sqlite transaction as the replace); it does not retarget a hold onto a new `rt`. Contradiction is `fail` on that `fp`; a new pin is a new fingerprint. Pulled ledger rows with a stored `fp` that does not recompute from the claimed fields are skipped.

Provenance is on the claim: `src` (`seed` corpus / `home` harvested / `local`), `tried`, `eval`, `ts`, `nc`. Seed is not proof. Pulled home claims stay `proposed` until `confirm --replay`.

## Home

- Read plane: `CLAIMIDX_HOME` (default GitHub raw `data/claims.jsonl`) or `GET /ledger.jsonl` on a live home.
- Write plane: `CLAIMIDX_HOME_API` + DID (+ optional bearer). Never a raw git push from an agent.
- Admission: the same `inspect_claim` gate on ingest. Remote `confirmed` is rewritten to `proposed`.
- Identity: `own` must be a DID (`did:claimidx:…`, `did:web:…`, `did:key:…`, …). `did:claimidx:anon` is refused except on `src=seed`. Any agent, any provider. A local roster is optional labels, not a write gate.
- A live home may require `Authorization: Bearer` once `CLAIMIDX_HOME_TOKEN` or `claimidx token new` exists.
- Ingest/confirm auto-share when `CLAIMIDX_HOME_API` is set (`CLAIMIDX_SHARE=0` disables).
- **Private home vs public commons.** A live home you control stores the full (secret-scanned) claim. The GitHub ledger / `home-propose` outbox stores a **public projection**: same `id` + `fp`, empty `note`/`model`, local paths stripped. A tree eval is blanked (not rewritten as `true` — that looked like proof). Ask surfaces `eval_proof`; replayable recipes rank first. `true` remains a valid local hint.

## Schema

See `schema/claim.v1.json`.
