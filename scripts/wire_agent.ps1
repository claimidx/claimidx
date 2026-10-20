# Boot this shell as a Claimidx owner. Any agent name, any provider.
# Usage: . .\scripts\wire_agent.ps1 <name>
# Example: . .\scripts\wire_agent.ps1 grok
#          . .\scripts\wire_agent.ps1 claude
#          . .\scripts\wire_agent.ps1 codex
param([string]$Agent = $(if ($env:CLAIMIDX_AGENT) { $env:CLAIMIDX_AGENT } else { "" }))
if (-not $Agent) { throw "pass an agent name: . .\scripts\wire_agent.ps1 <any-agent>" }
$slug = ($Agent.ToLower() -replace '[^a-z0-9._-]+', '-').Trim('-')
if (-not $slug) { $slug = "agent" }
$env:CLAIMIDX_AGENT = $slug
if (-not $env:CLAIMIDX_OWNER) { $env:CLAIMIDX_OWNER = "did:claimidx:$slug" }
if (-not $env:CLAIMIDX_DB) { $env:CLAIMIDX_DB = Join-Path $HOME ".claimidx\index.sqlite" }
New-Item -ItemType Directory -Force -Path (Split-Path $env:CLAIMIDX_DB) | Out-Null
Write-Host "wired $env:CLAIMIDX_OWNER db=$env:CLAIMIDX_DB"
if (Get-Command claimidx -ErrorAction SilentlyContinue) {
    claimidx --db $env:CLAIMIDX_DB whoami
}
