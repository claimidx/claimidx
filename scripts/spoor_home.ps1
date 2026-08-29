# Start Claimidx home on :7340 if nothing is already listening.
# Used by the ClaimidxHomeWatch scheduled task (every 5 minutes + at logon).
$ErrorActionPreference = "Continue"
$env:PYTHONPATH = "C:\Users\Administrator\Downloads\spoor-clone\src"
$env:CLAIMIDX_OWNER = "did:claimidx:grok"
$env:CLAIMIDX_AGENT = "grok"
$env:CLAIMIDX_HOME_API = "http://127.0.0.1:7340"
$py = "C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
$wd = "C:\Users\Administrator\Downloads\spoor-clone"
$log = Join-Path $env:USERPROFILE ".spoor\home.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$listening = Get-NetTCPConnection -LocalPort 7340 -State Listen -ErrorAction SilentlyContinue
if ($listening) { exit 0 }
$p = Start-Process -FilePath $py -ArgumentList @("-m", "spoor", "serve", "--host", "127.0.0.1", "--port", "7340") -WorkingDirectory $wd -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru
if (-not $p) { exit 1 }
exit 0
