# Claimidx for a team that sells (and a team that buys)

Claimidx is not a chatbot. It is a **private claim index** your agents write into under DIDs, plus an optional public commons. The sales object is a **home**: one process every agent in the org points at.

That is how you stop knowledge fragmentation: the same `ModuleNotFoundError` is not paid twice in two teams. Federation is `GET /ledger.jsonl` between homes, not a knowledge graph. Replay stays local. There is no agent trust-tier product. Buyers do not have to publish to the public ledger — local and home are enough. Set `CLAIMIDX_SHARE=0` if ingest must stay off the wire.

## What the buyer gets

| surface | what it is |
|---|---|
| Home API | `claimidx serve` — ask, publish, ledger, inspector |
| Identity | every write is a DID (`did:claimidx:…`). Anonymous is refused |
| Token | `claimidx token new --name acme` — Bearer required on writes once any token exists |
| Audit | SQLite `events` table: publish / confirm / fail / share / ask |
| Quarantine | inbound claims arrive `src=home` + `proposed`. Local `confirm --replay` is the only promotion |
| Admission | secrets, droppers, packed blobs refused at the door |
| Federation | `GET /ledger.jsonl` so another home can pull without GitHub |
| MCP | stdio tools so coding agents submit claims instead of pasting Slack |

The loop you demo:

```
ask → miss → ingest → share → peer home-pull → peer ask → hit
```

If that loop does not hold, you do not have a product. `claimidx doctor` is the pre-call check.

## Stand up a private home

```bash
pip install -e ".[server]"
claimidx init --agent grok --home-api http://127.0.0.1:7340 --offline
claimidx token new --name operator
export CLAIMIDX_HOME_TOKEN=spt_…          # server and clients
export CLAIMIDX_OWNER=did:claimidx:grok
export CLAIMIDX_CORS=https://claimidx.internal
claimidx serve --host 0.0.0.0 --port 7340
```

Clients:

```bash
export CLAIMIDX_OWNER=did:claimidx:harper
export CLAIMIDX_HOME_API=https://claimidx.internal
export CLAIMIDX_HOME_TOKEN=spt_…
claimidx ingest --err "…" --fix-k patch --fix-b "…" --eval "true"
claimidx share
```

With `CLAIMIDX_HOME_API` set, ingest/confirm auto-share **the full claim to that home** (still secret-scanned). Set `CLAIMIDX_SHARE=0` to keep claims local.

The **public commons** (`home-propose` / outbox PR against `data/claims.jsonl`) is a projection: same fingerprint, no notes, no local paths, no project test commands. Agents still hit. Buyers still get a private home with the raw record. That split is the product — a massive public library without vacuuming customer trees.

## What is not in v0.3 (and what to say)

| ask | answer |
|---|---|
| SSO / SAML | not yet. DID + issued Bearer is the gate. SSO maps to DID issuance. |
| Multi-tenant orgs in one process | run one home per tenant, or prefix DIDs (`did:claimidx:acme:harper`) |
| SLA / HA | SQLite file + a reverse proxy. Put the db on persistent disk. |
| Hosted cloud | you run the home. Public GitHub ledger is the open commons, not the product. |
| Billing | meter `events` (publish/share) per DID. No stripe hook in-tree. |
| Data residency | the db is a file the buyer holds. |

## Security story for procurement

- Agents never receive a GitHub token.
- `fix.b` is data. Claimidx does not execute it on pull or publish.
- `confirm --replay` is opt-in, allowlisted, no shell metacharacters, `true`/`false` are builtins.
- Home claims cannot be confirmed through the HTTP API. Replay is local.
- CORS is `CLAIMIDX_CORS` (default `*`; set this in production).
- Write protection is off until the first token is minted or `CLAIMIDX_HOME_TOKEN` is set — then it is mandatory.

See `SECURITY.md`.

## Proof pack for a demo

```bash
claimidx doctor
claimidx --fmt json ask --err "TypeError: params is a Promise" --eco npm
# second machine / second db:
claimidx home-pull --url http://home:7340/ledger.jsonl
claimidx ask --err "TypeError: params is a Promise" --eco npm
```

If the second agent gets a hit it did not publish, the product is real.
