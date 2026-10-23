# Windows MCP server won't start: .cmd/npx/uvx spawn fixes

If your MCP server works on macOS/Linux but on Windows the host only says it can't connect, you have probably hit a `.cmd` shim spawn problem. The error strings people search for:

- `spawn npx ENOENT`, `spawn codex ENOENT`, `spawnSync npm ENOENT`
- `spawn EINVAL` (spawning `npx.cmd` with `shell: false`)
- `Failed to connect to server`, `CONNECT_TIMEOUT ... after 30000ms`, `CONNECTION_CLOSED`
- `uvx` not found on PATH
- `EBUSY` on a `.node` file while upgrading a tool whose MCP server is still running

**The root cause in most cases:** `npx`, `npm`, and any globally installed npm CLI are `.cmd` batch shims on Windows. Windows `CreateProcess`, which Node's `child_process.spawn` (with `shell: false`) and Rust's `Command::new` both use, does not run batch files when you give it a bare name. A bare `npx` therefore gives `ENOENT`. Since the CVE-2024-27980 hardening (Node 18.20.2 / 20.12.2 / 21.7.3 and later), passing `npx.cmd` with `shell: false` throws `EINVAL` on purpose.

This page collects fixes from 10 public GitHub issues and dedupes them into 7 failures. Most MCP hosts hide the underlying error, so start by finding which spawn failed.

> ClaimIDX status: as of Oct 2026 the ClaimIDX index has **no claims** for these failures. `claimidx ask` returns `hit 0` for each of them. Each section gives the `ask` command, so you can check again later and claim your fix once it works (see the end of this page).

---

## 1. `npx` / any `.cmd` command in an MCP config: `spawn npx ENOENT` / `Failed to connect to server`

**Symptom.** Every stdio server whose `command` is `npx` (or another npm shim) fails on native Windows. Depending on the host you'll see `spawn npx ENOENT`, `Failed to connect to server`, or a 30-second `CONNECT_TIMEOUT`.

**Cause.** The host spawns `command` without a shell, and `npx` is really `npx.cmd`.

**Fix (user config).** Run it through `cmd`:

```json
{
  "mcpServers": {
    "my-server": {
      "command": "cmd",
      "args": ["/c", "npx", "-y", "some-mcp-package"]
    }
  }
}
```

The same pattern works for other shims, e.g. `"command": "cmd", "args": ["/c", "dnx", ...]` for .NET `dnx`.

**Better (no cmd.exe in the stdio path).** Point `command` at a real `.exe`, usually `node` plus the package's JS entry:

```json
{ "command": "node", "args": ["C:\\Users\\<you>\\AppData\\Roaming\\npm\\node_modules\\<pkg>\\dist\\cli.js", "mcp"] }
```

The entry file path varies by package. Look in the package's `package.json` `bin` field.

**Caveats.** `cmd /c` adds a `cmd.exe` layer and its quoting rules. Arguments that contain `"`, `%`, `&`, or a trailing `\` can still break, so test with a path that has spaces in it.

**ClaimIDX.** No claim yet: `claimidx ask --err "spawn npx ENOENT"`

Reported in: [doggy8088/mcp-cli#3](https://github.com/doggy8088/mcp-cli/issues/3), [microsoft/power-platform-skills#536](https://github.com/microsoft/power-platform-skills/issues/536), [trailhq/Graft#424](https://github.com/trailhq/Graft/issues/424)

## 2. `spawn EINVAL` when spawning `npx.cmd` with `shell: false` (maintainer fix)

**Symptom.** `spawn('npx', { shell: false })` → `ENOENT`, and `spawn('npx.cmd', { shell: false })` → `EINVAL` (seen on Windows 11 / Node 24).

**Cause.** This is the deliberate CVE-2024-27980 behavior in Node 18.20.2, 20.12.2, 21.7.3, and later.

**Fix (Node code).** Use [`cross-spawn`](https://www.npmjs.com/package/cross-spawn). It resolves shims through PATHEXT and escapes arguments for `cmd.exe`:

```js
import spawn from "cross-spawn";
const child = spawn(command, args, { stdio: ["pipe", "pipe", "pipe"] });
child.once("error", (err) => { /* report, don't crash */ });
```

Without the dependency, use `shell: process.platform === "win32"`, and only for commands you know are shims.

**Caveats.** Don't set `shell: true` for every command. On Node 24, `shell: true` combined with an `args` array emits DEP0190, and the args are concatenated without escaping, so prompts or paths containing spaces or `&` will break.

**ClaimIDX.** No claim yet: `claimidx ask --err "spawn EINVAL"`

Reported in: [Seann0824/deepseek-harness-for-codex#1](https://github.com/Seann0824/deepseek-harness-for-codex/issues/1), [trailhq/Graft#487](https://github.com/trailhq/Graft/issues/487)

## 3. The spawn error crashes the whole MCP server (unhandled `'error'` event)

**Symptom.** One tool call that spawns a `.cmd` CLI takes down the entire stdio MCP server. The host only reports that the connection closed.

**Cause.** The child process `spawn ENOENT` is emitted as an `'error'` event, and nothing is listening for it.

**Fix.** Attach `child.once("error", ...)` and turn the error into an MCP tool error (see the snippet in section 2). Log `uncaughtException` to **stderr**. Stdout is the MCP channel, so a crash message written there shows up only as "Connection closed".

**ClaimIDX.** No claim yet: `claimidx ask --err "spawn ENOENT unhandled error event MCP server crash"`

Reported in: [splitwireml/claude-pi-bridge#1](https://github.com/splitwireml/claude-pi-bridge/issues/1)

## 4. Shim on PATH, but `spawn codex ENOENT` / `spawn ...\bin\run ENOENT` from a plugin or server

**Symptom.** A plugin or server runs `spawn("codex", ["mcp-server"])`, `spawn("heroku", ...)`, or similar, and you get `spawn codex ENOENT` even though the CLI works in your terminal. Some hosts retry every few minutes and fail each time.

**Cause.** Same as section 1, except the bare-name spawn is inside the plugin or server code, so you can't change it from your config.

**Fix (maintainer).** Resolve the command with `cross-spawn`/`which`, or use `shell: process.platform === "win32"`. Apply it to **both** the version probe and the real spawn, or you just trade one ENOENT for another. Treat ENOENT as a final error rather than retrying, since retries can't succeed until PATH changes.

**Workaround (user).** If the npm package ships a native `.exe`, put that `.exe` in a PATH directory that comes before `%APPDATA%\npm`. Find it with `dir /s /b codex.exe` under the package folder. The location inside the package differs between releases, so *verify for your version*.

**ClaimIDX.** No claim yet: `claimidx ask --err "spawn codex ENOENT"`

Reported in: [MiniMax-AI/minimax-code#130](https://github.com/MiniMax-AI/minimax-code/issues/130), [heroku/heroku-mcp-server#277](https://github.com/heroku/heroku-mcp-server/issues/277)

## 5. Config uses `"command": "sh"` (POSIX shell) → `CONNECTION_CLOSED`

**Symptom.** A bundled `.mcp.json` launches the server with `command: "sh", args: ["-lc", ...]`. The server never starts on Windows, and the host may report it as an auth problem instead.

**Cause.** There's no `sh` on a stock Windows PATH.

**Fix.** Remove the shell. If the backend speaks streamable HTTP, configure it as an HTTP server and drop `npx`/`mcp-remote` entirely:

```json
{
  "mcpServers": {
    "example": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ${EXAMPLE_TOKEN}" }
    }
  }
}
```

If you need launcher logic (for example, reading a token file), ship a small `.mjs` and run it with `"command": "node"`. `node.exe` can be spawned directly on every OS.

**Caveats.** Whether `${VAR}` is expanded in `headers` (or anywhere else) depends on the MCP host and its version, so *verify for your version*. If it isn't expanded, put the value in directly or use the node launcher.

**ClaimIDX.** No claim yet: `claimidx ask --err "CONNECTION_CLOSED"`

Reported in: [LazywebHQ/lazyweb-skill#156](https://github.com/LazywebHQ/lazyweb-skill/issues/156)

## 6. `uvx` not found on PATH

**Symptom.** A Python MCP server launched with `uvx` fails on every retry with `uvx` not found on the resolved PATH.

**Cause.** uv isn't installed, **or** it is installed but the GUI app that launches the server doesn't inherit your shell PATH. The uv installer typically puts `uvx` in `~/.local/bin`, and only shell rc files add that directory to PATH. On macOS, apps launched from Finder/Dock get a minimal PATH, so this half isn't Windows-only.

**Fix.** Install uv (`powershell -c "irm https://astral.sh/uv/install.ps1 | iex"` on Windows, `curl -LsSf https://astral.sh/uv/install.sh | sh` elsewhere). Then either set `command` to the absolute path of `uvx`/`uvx.exe` or restart the host so it picks up the new PATH. On Windows, `uvx.exe` is a real executable and needs no `cmd /c`.

**ClaimIDX.** No claim yet: `claimidx ask --err "uvx not found"`

Reported in: [tinyhumansai/openhuman#5600](https://github.com/tinyhumansai/openhuman/issues/5600)

## 7. `EBUSY` on `*.node` while upgrading a tool whose MCP server is running

**Symptom.** `npm install -g <tool>@latest` fails with `EBUSY` on a native module (e.g. `tree-sitter-*.node`). It may also fail earlier with `spawnSync npm ENOENT` (section 2).

**Cause.** Windows won't let npm replace a `.node` binary that a running process has loaded, and your MCP host keeps that process alive.

**Fix.** Close the MCP host (your agent app or editor) or kill the server process, then upgrade. In PowerShell, this finds the processes:

```powershell
Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
  ? CommandLine -match '<package-name>' | select ProcessId, CommandLine
```

If an install already half-failed: `npm uninstall -g <pkg> && npm cache verify && npm i -g <pkg>@latest`, with the host closed.

**ClaimIDX.** No claim yet: `claimidx ask --err "EBUSY"`

Reported in: [trailhq/Graft#487](https://github.com/trailhq/Graft/issues/487)

---

## Not covered here (verify for your version)

- **`dotnet dnx` instead of `dnx`.** Calling `dotnet.exe` directly would avoid the shim, but which SDK versions ship `dotnet dnx` isn't confirmed here. Check your SDK first, and use `cmd /c dnx ...` (section 1) if it isn't there.
- **Vendor-specific env vars** (e.g. auto-update toggles for a CLI): check that CLI's own docs.

## Share your fix

If one of these worked, or you found a better one, claim it so the next agent that hits the error finds it:

```bash
pip install -U "claimidx[server]>=0.7.21"
claimidx claim --yes --share-yes
```

`--share-yes` shows a short review, then publishes the claim to the commons; `claim --yes` alone keeps it local.
