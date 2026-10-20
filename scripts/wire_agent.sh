#!/usr/bin/env bash
# Boot an agent process as a Claimidx owner. Any agent name, any provider.
# Usage: source scripts/wire_agent.sh <name>
set -euo pipefail
AGENT="${1:-${CLAIMIDX_AGENT:-}}"
if [ -z "$AGENT" ]; then
  echo "pass an agent name: source scripts/wire_agent.sh <any-agent>" >&2
  return 1 2>/dev/null || exit 1
fi
slug=$(printf '%s' "$AGENT" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g; s/-\+/-/g; s/^-//; s/-$//')
[ -n "$slug" ] || slug=agent
export CLAIMIDX_AGENT="$slug"
export CLAIMIDX_OWNER="${CLAIMIDX_OWNER:-did:claimidx:${slug}}"
export CLAIMIDX_DB="${CLAIMIDX_DB:-$HOME/.claimidx/index.sqlite}"
mkdir -p "$(dirname "$CLAIMIDX_DB")"
echo "wired $CLAIMIDX_OWNER db=$CLAIMIDX_DB"
command -v claimidx >/dev/null && claimidx --db "$CLAIMIDX_DB" whoami || true
