# Same as ask.sh — works on Windows PowerShell.
claimidx --fmt json ask `
  --err "TypeError: params is a Promise" `
  --eco npm `
  --rt node@20 `
  --dep next@15.0.0
