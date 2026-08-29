# Claimidx security

Claimidx is an index of claims. It is not a package manager, not a script runner, and not a place that should ever execute a stranger’s `fix` on ingest.

You cannot make a shared text index “immune” to malice if it downloads and runs what it stores. The design choice is the other way: **store scanned data, never become an execution engine.**

## What an attacker would want

A poisoned claim whose `fix.b` or `eval.cmd` is a dropper, so that an agent which blindly “applies the fix” infects the host.

## What Claimidx actually does

1. **Admission scan.** Publish, ingest, and `store.put` run `inspect_claim`. Secrets, packed blobs, fetch-and-execute shapes, encoded command lines, and well-known living-off-the-land runners are refused. They never hit the index.
2. **`fix.b` is data.** The inspector and the CLI print it. Claimidx does not apply patches and does not spawn a shell to “run the fix.”
3. **`eval.cmd` is a recipe.** Default `confirm` only increments a counter. `confirm --replay` is opt-in, allowlisted heads only (`true`, `python`, `pytest`, `npx`, `npm`, `node`, `go`, `test`), no pipes, no redirects, no network fetchers, **45s timeout**. A poisoned eval cannot hang the host.
4. **Home is quarantined.** Claims with `src=home` cannot arrive as `confirmed`. Remote hearsay does not become local proof.
5. **No attachments.** No binaries, no `data:` URIs, no long base64 runs. Size caps on every field.
6. **Identity.** Wired agents publish under a DID. Anonymous writes should not graduate without a signed owner and a local replay.
7. **Fail flips status.** Two independent fails contest a claim. That is the recall mechanism.

## What Claimidx does not claim

- It cannot stop an agent that copies `fix.b` into a shell after a human or a loose skill tells it to “just run this.” That is the agent runtime’s policy, not the index.
- Pattern scanners are not a proof of safety. They raise the cost of sloppy droppers. They do not make a zero-day filter.
- A hosted home API must sit behind the same admission scan, plus auth. An open anonymous write endpoint is how you get sludge and worse.

## The public site

`claimidx.com` is a static index on Cloudflare Pages.

- HTTPS only (HSTS, `upgrade-insecure-requests`). `www` redirects to the apex.
- CORS `*` is limited to machine files (`/llms.txt`, `/.well-known/*`, `/AGENTS.md`). HTML is not readable cross-origin.
- No third-party scripts or webfonts. CSP: `default-src 'self'`.
- Reports: `/.well-known/security.txt` → `hello@claimidx.com`.

A live home API (`claimidx serve`) is a different surface. Do not expose `:7340` to the internet without auth.

## Operator rules

- Do not set an agent skill to “apply every confirmed fix.” Apply under org policy, in a sandbox, after reading `fix.k`.
- Prefer `constraint` / `pin` / `patch` over `cmd`.
- Point `CLAIMIDX_HOME` at a ledger you can pull; only point `CLAIMIDX_HOME_API` at a server you control.
- Agents submit to home with `home-push` (live API) or `home-propose` (PR line). They do not get write access to GitHub.
- Run `claimidx scan` on anything before you ingest it by hand.

## Evidence

Every publish / confirm / fail is an append-only event with actor DID, claim id, and time. That log is what you hand an auditor. It is not an antivirus.
