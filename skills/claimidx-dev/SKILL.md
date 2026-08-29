---
name: spoor-dev
description: Develop and ship the Claimidx product on this Windows machine. Use when editing Claimidx, pip installing it, restarting the home on :7340, or pushing to claimidx/claimidx.
---

# Claimidx development (this machine)

Canonical clone: `C:\Users\Administrator\Downloads\spoor-clone`  
GitHub: `https://github.com/claimidx/claimidx` branch **`main` only**.  
Do not use `C:\Users\Administrator\spoor` (old v0.1.0).

## Install / reinstall

`claimidx serve` locks `...\Python313\Scripts\claimidx.exe`. Stop it first or pip hits WinError 32.

```powershell
Get-NetTCPConnection -LocalPort 7340 -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process -Name claimidx -ErrorAction SilentlyContinue | Stop-Process -Force
cd C:\Users\Administrator\Downloads\spoor-clone
pip install -e ".[server,dev]"
python -m pytest -q
```

Scripts after install: `claimidx` and `claimidx-mcp` (stdio; do not run `claimidx-mcp` in a raw terminal).

## Runtime

```
CLAIMIDX_OWNER=did:claimidx:grok
CLAIMIDX_AGENT=grok
CLAIMIDX_HOME_API=http://127.0.0.1:7340
```

Config/db: `~\.spoor\config.json`, `~\.spoor\index.sqlite`.

```powershell
$env:CLAIMIDX_OWNER="did:claimidx:grok"; $env:CLAIMIDX_AGENT="grok"
claimidx serve --host 127.0.0.1 --port 7340
```

MCP for Grok: `~/.grok/config.toml` `[mcp_servers.spoor] command = "claimidx-mcp"`. New chat after changing it.

## Ship

Work in the clone. `git add -A`, commit, `git push origin main`. Do not park the complete tree on a side branch.

Memory: `memory/PROJECT_MEMORY.md`, `docs/ARCHITECTURE.md`, `memory/NEXT_TASKS.md`.
