"""Admission policy. Claimidx stores claims. It does not run strangers' code.

fix.b is data. eval.cmd is a recipe. Neither is executed on pull or publish.
Replay is opt-in, allowlisted, no shell metacharacters, no network fetchers.
"""

from __future__ import annotations

import re
import shlex

from .public import pin_error
from .security import reject_secrets


class PolicyError(ValueError):
    pass


# Size caps — binaries and packed scripts do not fit, and should not.
MAX_FIX = 4000
MAX_EVAL = 400
MAX_NOTE = 240
MAX_ERR = 280
MAX_BASE64_RUN = 80

# Fetch-and-execute. Applied to every field including err (Maven logs must survive).
_DROPPER_PAYLOAD = [
    re.compile(r"\b(curl|wget|fetch|Invoke-WebRequest|iwr)\b.{0,80}\|\s*(sh|bash|zsh|cmd|powershell|pwsh|python|perl)\b", re.I | re.S),
    re.compile(r"\b(iex|invoke-expression)\b", re.I),
    re.compile(r"\b(mshta|certutil|bitsadmin|regsvr32|rundll32|wscript|cscript|hh\.exe)\b", re.I),
    re.compile(r"\bchmod\s+\+x\b.{0,40}\b(curl|wget)\b", re.I | re.S),
    re.compile(r"/dev/tcp/"),
    re.compile(r"data:[^;]+;base64,", re.I),
    re.compile(r"MZ[\x00-\x08]{0,2}PE\x00|\x7fELF"),
    re.compile(r"^\s*#!/bin/(ba)?sh", re.M),
    re.compile(r"\bpowershell\b.{0,60}\b-enc(odedcommand)?\b", re.I),
    re.compile(r"\bbash\s+-c\b.{0,40}\b(curl|wget)\b", re.I),
    re.compile(r"\bnc\s+-[el]", re.I),
    re.compile(r"\b(reverse.?shell|bind.?shell)\b", re.I),
]
# Code-shaped. Not applied to err/note: `:compile (default-compile)` is a Maven log.
# call/popen/run require `(` so subprocess.CalledProcessError is documentation, not a dropper.
# compile( skips re.compile and Maven `:compile (`.
_DROPPER_CODE = [
    re.compile(r"\b(fromhex|fromcharcode|charcodeat)\b.{0,20}\b(exec|eval|compile)\b", re.I | re.S),
    re.compile(r"\b(os\.system|subprocess\.(?:call|popen|run)\s*\(|popen\()", re.I),
    re.compile(r"\bexec\s*\(|\beval\s*\(|(?<!re\.)(?<!:)\bcompile\s*\("),
]

_LONG_B64 = re.compile(rf"[A-Za-z0-9+/]{{{MAX_BASE64_RUN},}}={{0,2}}")

ALLOWED_EVAL_HEADS = {
    "true",
    "false",
    "test",
    "python",
    "python3",
    "pytest",
    "npx",
    "npm",
    "node",
    "go",
    "uv",
    "cargo",
    "rustc",
    "docker",
}
# cmd-kind fix.b is data, but naive agents may run it. Wider than eval; still no shell.
ALLOWED_CMD_HEADS = ALLOWED_EVAL_HEADS | {
    "git",
    "pip",
    "pip3",
    "bundle",
    "bundler",
    "composer",
    "make",
    "mvn",
    "gradle",
    "alembic",
    "pnpm",
    "yarn",
    "yarnpkg",
    "apt-get",
    "apt",
    "keytool",
    "java",
    "javac",
    "helm",
    "kubectl",
    "poetry",
    "pipenv",
    "corepack",
    "bun",
}

DENIED_EVAL_HEADS = {
    "curl",
    "wget",
    "nc",
    "ncat",
    "ssh",
    "scp",
    "rsync",
    "sudo",
    "bash",
    "sh",
    "zsh",
    "pwsh",
    "powershell",
    "cmd",
    "mshta",
    "certutil",
    "python2",
    "perl",
    "ruby",
    "php",
    "lua",
}

DENIED_EVAL_TOKENS = {
    "sudo",
    "rm",
    "dd",
    "mkfs",
    "chmod",
    "chown",
    "curl",
    "wget",
    "ssh",
    "nc",
    "ncat",
    "exec",
    "eval",
    "source",
    "os.system",
    "urllib",
    "requests",
    "socket",
    "subprocess",
    "pty",
}


def _scan_dropper(text: str, field: str) -> None:
    if not text:
        return
    if len(text) > (MAX_FIX if field == "fix" else MAX_EVAL if field == "eval" else 2000):
        raise PolicyError(f"{field} exceeds size cap")
    if _LONG_B64.search(text or ""):
        raise PolicyError(f"{field} contains a packed blob; refuse to store")
    pats = list(_DROPPER_PAYLOAD)
    if field not in ("err", "note"):
        pats.extend(_DROPPER_CODE)
    for pat in pats:
        if pat.search(text):
            raise PolicyError(f"{field} matches a dropper-shaped pattern; refuse to store")


def reject_payload(text: str | None, field: str = "fix") -> None:
    if not text:
        return
    reject_secrets(text)
    _scan_dropper(text, field)


def _prep_eval(raw: str) -> str:
    """Unix recipes are canonical. Rewrite Windows paths so shlex does not eat backslashes.

    Handles drive letters (`C:\\foo`), UNC roots (`\\\\server\\share`), and mixed separators.
    """
    if "\\" not in raw:
        return raw
    out = re.sub(r"([A-Za-z]):\\", r"\1:/", raw)
    out = re.sub(r"(^|[\s\"'])\\\\+", r"\1//", out)
    return out.replace("\\", "/")


def _norm_head(token: str) -> str:
    name = token.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


# Replay must not fetch packages. Local editable installs still prove a tree.
_LOCAL_PIP = re.compile(r"\bpip\b.+\binstall\b.*(\s-e\s|\s\.(?:\s|$))", re.I)


def _network_pip_install(parts: list[str]) -> bool:
    """True when argv is `pip install <pkg>` / `uv pip install <pkg>`, not python -c text."""
    lower = [p.lower() for p in parts]
    for i, p in enumerate(lower):
        if p == "pip" and i + 1 < len(lower) and lower[i + 1] == "install":
            rest = " ".join(lower[i:])
            return not _LOCAL_PIP.search(rest)
    return False


_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.")
ALLOWED_EVAL_ENV = {
    "GOTOOLCHAIN",
    "GOFLAGS",
    "GO111MODULE",
    "GOSUMDB",
}
_QUOTED = re.compile(r"""(?:'[^']*'|"[^"]*")""")


def _unquoted_meta(raw: str) -> bool:
    """Shell metacharacters only count outside quotes. node -e 'a; b' is argv, not a pipeline."""
    stripped = _QUOTED.sub(" ", raw)
    return any(tok in stripped for tok in (";", "|", "&", ">", "<", "`", "\n", "$(", "&&", "||"))


def split_eval(cmd: str) -> tuple[dict[str, str], list[str]]:
    """Peel KEY=val prefixes so GOTOOLCHAIN=local go build is a go eval."""
    parts = shlex.split(_prep_eval(cmd))
    env: dict[str, str] = {}
    while parts and _ENV_ASSIGN.match(parts[0]):
        key, _, val = parts[0].partition("=")
        env[key] = val
        parts = parts[1:]
    return env, parts


def eval_allowed(cmd: str, *, heads: set[str] | None = None) -> tuple[bool, str]:
    raw = (cmd or "").strip()
    if not raw:
        return False, "empty eval"
    if len(raw) > MAX_EVAL:
        return False, "eval too long"
    if _unquoted_meta(raw):
        return False, "shell metacharacter denied"
    try:
        _env, parts = split_eval(raw)
    except ValueError as e:
        return False, f"unparseable eval: {e}"
    if not parts:
        return False, "empty eval"
    for key in _env:
        if key not in ALLOWED_EVAL_ENV:
            return False, f"eval env not allowlisted: {key}"
    head = _norm_head(parts[0])
    denied = {h.lower() for h in DENIED_EVAL_HEADS}
    allowed = {h.lower() for h in (heads or ALLOWED_EVAL_HEADS)}
    if head in denied:
        return False, f"eval head denied: {head}"
    if head not in allowed:
        return False, f"eval head not allowlisted: {head}"
    if _network_pip_install(parts):
        return False, "eval network pip install denied"
    lower = {p.lower() for p in parts}
    if lower & DENIED_EVAL_TOKENS:
        return False, "eval token denied"
    joined = " ".join(parts).lower()
    for tok in ("os.system", "subprocess", "urllib", "__import__"):
        if tok in joined:
            return False, "eval imports a dangerous module"
    if any(p.lower() == "socket" for p in parts):
        return False, "eval imports a dangerous module"
    return True, "ok"


def reject_eval(cmd: str | None) -> None:
    cmd = cmd or ""
    if not cmd.strip():
        return
    reject_secrets(cmd)
    _scan_dropper(cmd, "eval")
    ok, reason = eval_allowed(cmd)
    if not ok:
        raise PolicyError(reason)


def require_identity(own: str, src: str = "local") -> None:
    """Anonymous local writes are refused. Seed corpus is the exception."""
    if src == "seed":
        return
    did = (own or "").strip()
    if not did or did in {"did:claimidx:anon", "anon"}:
        raise PolicyError("anonymous writes refused; set CLAIMIDX_OWNER to a DID (did:claimidx:…)")
    if not did.startswith("did:"):
        raise PolicyError("owner must be a DID (did:claimidx:…)")


def inspect_claim(*, err: str, fix_k: str, fix_b: str, eval_cmd: str, note: str = "", own: str = "", src: str = "local") -> None:
    """Gate used by publish/ingest/pull. Raises PolicyError or SecretError."""
    reject_secrets(err)
    reject_secrets(note)
    reject_secrets(own)
    require_identity(own, src=src)
    _scan_dropper(err, "err")
    _scan_dropper(note, "note")
    reject_payload(fix_b, "fix")
    bad_pin = pin_error(fix_k, fix_b)
    if bad_pin:
        raise PolicyError(bad_pin)
    if fix_k == "cmd":
        ok, reason = eval_allowed(fix_b, heads=ALLOWED_CMD_HEADS) if len(fix_b) <= MAX_EVAL else (False, "cmd fix too long")
        if not ok:
            if not _seed_cmd_ok(fix_b):
                raise PolicyError(f"cmd fix denied: {reason.replace('eval head', 'cmd head', 1)}")
    reject_eval(eval_cmd)


def _seed_cmd_ok(cmd: str) -> bool:
    """Narrow exceptions for already-shipped seed commands. Not a general hole."""
    allowed = {
        "npx playwright install --with-deps chromium",
        "go clean -modcache && go mod download",
        "python -m venv .venv && .venv/bin/pip install -e .",
        "sleep $((2 ** attempt)) && retry",
    }
    return cmd.strip() in allowed


def quarantine(claim) -> None:
    """Remote/home claims never arrive confirmed."""
    if getattr(claim, "src", "local") == "home":
        if claim.st in ("confirmed",):
            claim.st = "proposed"
        if claim.nc or claim.nf:
            # keep counters as hearsay but do not treat as local proof
            pass
