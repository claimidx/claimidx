"""Opt-in eval replay. Never used on pull or publish."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .fingerprint import runtime_proof_key
from .policy import _LOCAL_PIP, _norm_head, eval_allowed, split_eval


def project_python(cwd: str | os.PathLike[str] | None) -> str | None:
    """The tree's own interpreter (`.venv`/`venv` under cwd), when it has one.

    A claim about a project is about that project's environment: an agent's
    shell PATH rarely has the venv active when a hook fires, and replaying
    `python -c "import x"` under the wrong interpreter turns a real fix into
    a false miss.
    """
    if not cwd:
        return None
    root = Path(cwd)
    for venv in (".venv", "venv"):
        cand = root / venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if cand.is_file():
            return str(cand)
    return None


def project_bin(head: str, cwd: str | os.PathLike[str] | None) -> str | None:
    """`<cwd>/.venv/bin/<head>` or `<cwd>/node_modules/.bin/<head>` when the tree ships the tool itself."""
    if not cwd:
        return None
    root = Path(cwd)
    base = _norm_head(head)
    candidates = []
    wrapper = {"gradle": "gradlew", "gradlew": "gradlew", "mvn": "mvnw", "mvnw": "mvnw"}.get(base)
    if wrapper:
        # A tree that ships gradlew/mvnw pins its own build tool; use it before anything on PATH.
        ext = ".bat" if wrapper == "gradlew" else ".cmd"
        candidates += [root / (wrapper + ext)] if os.name == "nt" else [root / wrapper]
    for venv in (".venv", "venv"):
        if os.name == "nt":
            candidates += [root / venv / "Scripts" / f"{base}.exe", root / venv / "Scripts" / f"{base}.cmd"]
        else:
            candidates.append(root / venv / "bin" / base)
    if os.name == "nt":
        candidates.append(root / "node_modules" / ".bin" / f"{base}.cmd")
    else:
        candidates.append(root / "node_modules" / ".bin" / base)
    for cand in candidates:
        if cand.is_file():
            return str(cand)
    return None


def resolve_argv(parts: list[str], cwd: str | os.PathLike[str] | None = None) -> list[str]:
    """Map an allowlisted eval recipe onto this OS's real executable.

    Unix recipes stay canonical (`python -c`, `npx tsc`). `python`/`python3`
    follow CLAIMIDX_PYTHON, then the tree's own venv under cwd, then PATH,
    then this interpreter. Other heads resolve via PATHEXT on Windows.
    """
    if not parts:
        return parts
    name = _norm_head(parts[0])
    if name in ("python", "python3"):
        pinned = (os.environ.get("CLAIMIDX_PYTHON") or "").strip()
        if pinned:
            return [pinned, *parts[1:]]
        own = project_python(cwd)
        if own:
            return [own, *parts[1:]]
        found = _which(parts[0])
        if found:
            return [found, *parts[1:]]
        return [sys.executable, *parts[1:]]
    own = project_bin(parts[0], cwd)
    if own:
        return [own, *parts[1:]]
    found = _which(parts[0])
    return [found or parts[0], *parts[1:]]


def _which(head: str) -> str | None:
    names = [head]
    if os.name == "nt":
        base = _norm_head(head)
        names.extend([base, f"{base}.exe", f"{base}.cmd", f"{base}.bat"])
    seen: set[str] = set()
    for n in names:
        key = n.lower()
        if key in seen:
            continue
        seen.add(key)
        found = shutil.which(n)
        if found:
            return found
    return None


@dataclass
class ReplayResult:
    allowed: bool
    ran: bool
    rc: int | None
    expect: int
    held: bool
    reason: str
    stdout: str = ""
    stderr: str = ""
    env: str = ""  # observed executing runtime, e.g. py@3.12 / node@20
    ms: int = 0
    net: str = ""  # offline | open | unenforced
    net_how: str = ""  # bwrap | interpreter | docker | python-flags | cargo-net-offline | head

    def as_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "ran": self.ran,
            "rc": self.rc,
            "expect": self.expect,
            "held": self.held,
            "reason": self.reason,
            "stdout": self.stdout[-400:],
            "stderr": self.stderr[-400:],
            "env": self.env,
            "ms": int(self.ms or 0),
            "net": self.net,
            "net_how": self.net_how,
        }

    def is_hint(self) -> bool:
        """true/false builtins, version tautologies, and unmet trees cannot mint nc."""
        r = self.reason or ""
        return r == "builtin" or r.startswith("eval-precondition") or r.startswith("eval-untrusted")


# A replay the gate refused to record. `held` means a recorded hold, so the replay object must not say
# "held" anywhere (flag or reason) once the gate said no; `eval_passed` keeps what the eval itself did.
NOT_RECORDED_REASON = "eval-passed-not-recorded"


def refused_replay_info(info: dict | None) -> dict:
    """The replay dict for an eval that passed but was not recorded: held false, eval_passed true, no "held" reason."""
    out = {**(info or {}), "held": False, "eval_passed": True, "recorded": False}
    if str(out.get("reason") or "").startswith("held"):
        out["reason"] = NOT_RECORDED_REASON
    rerun = out.get("rerun")
    if isinstance(rerun, dict) and (rerun.get("held") or str(rerun.get("reason") or "").startswith("held")):
        out["rerun"] = refused_replay_info(rerun)
    return out


def not_recorded_info(info: dict | None, eval_passed: bool) -> dict:
    """A refusal that recorded nothing: relabel a passing eval as not held, or mark a miss explicitly."""
    return refused_replay_info(info) if eval_passed else eval_miss_info(info)


def eval_miss_info(info: dict | None) -> dict:
    """The replay dict for an eval that ran and did not pass: eval_passed is false, said explicitly."""
    return {**(info or {}), "eval_passed": False}


_TREE_MARKERS = {
    "npx": ("package.json", "tsconfig.json"),
    "npm": ("package.json",),
    "go": ("go.mod", "go.work"),
    "cargo": ("Cargo.toml",),
    "rustc": ("Cargo.toml",),
    "docker": ("Dockerfile", "docker-compose.yml", "compose.yml"),
    "pytest": ("pytest.ini", "pyproject.toml", "setup.cfg", "tests", "test", "conftest.py"),
    "mvn": ("pom.xml",),
    "mvnw": ("pom.xml",),
    "gradle": ("build.gradle", "build.gradle.kts"),
    "gradlew": ("build.gradle", "build.gradle.kts"),
    "composer": ("composer.json",),
    "bundle": ("Gemfile",),
    "bundler": ("Gemfile",),
    "gem": ("Gemfile",),
    "php": ("composer.json",),
    "make": ("Makefile", "makefile"),
}
_ENV_HEADS = {"python", "python3", "node"}


def observe_env(argv: list[str]) -> str:
    """Executing runtime of a resolved eval argv. Empty if not python/node."""
    if not argv:
        return ""
    exe = argv[0]
    base = os.path.basename(exe).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base in {"python", "python3", "pythonw"}:
        return _observe_py(exe)
    if base == "node":
        return _observe_node(exe)
    return ""


def _observe_py(exe: str) -> str:
    try:
        if os.path.exists(exe) and os.path.exists(sys.executable) and os.path.samefile(exe, sys.executable):
            v = sys.version_info
            return f"py@{v.major}.{v.minor}"
    except OSError:
        pass
    try:
        proc = subprocess.run(
            [exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    ver = (proc.stdout or "").strip()
    if re.fullmatch(r"\d+\.\d+", ver):
        return f"py@{ver}"
    return ""


def _observe_node(exe: str) -> str:
    try:
        proc = subprocess.run(
            [exe, "-p", "process.versions.node"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    ver = (proc.stdout or "").strip()
    m = re.match(r"(\d+)", ver)
    if m:
        return f"node@{m.group(1)}"
    return ""


def _eval_needs_env(cmd: str) -> bool:
    try:
        _, parts = split_eval(cmd)
    except ValueError:
        return False
    if not parts:
        return False
    return _norm_head(parts[0]) in _ENV_HEADS


def replay_records_hold(claim_rt: str, result: ReplayResult, cmd: str = "") -> tuple[bool, str]:
    """Whether a held replay may mint nr.

    Python/node evals observe the executing interpreter. That env is
    required: claim.rt must be set and match at runtime_proof_key grain.
    Other heads keep prior confirm-replay behavior.
    """
    if result.is_hint() or not result.held:
        return False, result.reason
    if not _eval_needs_env(cmd):
        return True, "held"
    observed = (result.env or "").strip()
    if not observed:
        return False, "hold requires observed env"
    declared = (claim_rt or "").strip()
    if not declared:
        return False, f"hold requires rt matching observed env ({observed})"
    if runtime_proof_key(declared) != runtime_proof_key(observed):
        return False, f"hold env mismatch: claim.rt={declared} observed={observed}"
    return True, "held"


_NAMED_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*\.(?:py|js|mjs|cjs|ts|tsx|jsx|rs|go|java|sh|toml|json|ya?ml|txt|cfg|ini)$")


def _precondition(head: str, cwd: str | None, cmd: str = "") -> str | None:
    markers = _TREE_MARKERS.get(head)
    blob = cmd or ""
    try:
        _env, parts = split_eval(blob)
    except ValueError:
        parts = []
    tokens = {p.lower() for p in parts}
    if head in {"python", "python3", "node", "rustc", "javac"}:
        # `python check.py` outside the tree is not a miss; it is a recipe with nothing to run against.
        for tok in parts[1:]:
            if tok.startswith("-"):
                break
            if _NAMED_FILE.match(tok) and ".." not in tok.split("/"):
                if not os.path.exists(os.path.join(cwd or os.getcwd(), tok)):
                    return f"eval-precondition: no {tok} in cwd"
                break
    if head in {"python", "python3"} and _LOCAL_PIP.search(blob):
        markers = ("pyproject.toml", "setup.py", "setup.cfg")
    elif head in {"python", "python3"} and "pytest" in tokens:
        # An argv token, not a substring: a -c payload or a path containing "pytest" is not a pytest run.
        markers = _TREE_MARKERS["pytest"]
    elif "make" in tokens and head in {"python", "python3", "node", "test", "make"}:
        markers = _TREE_MARKERS["make"]
    if not markers:
        return None
    root = cwd or os.getcwd()
    if any(os.path.exists(os.path.join(root, name)) for name in markers):
        return None
    return f"eval-precondition: no {'/'.join(markers)} in cwd"


_BLOCK_SIGNALS = (
    "URLError",
    "ENETUNREACH",
    "getaddrinfo",
    "claimidx-replay-offline",
    "Network is unreachable",
    "WSAENETUNREACH",
    "WinError 10051",
    "nodename nor servname",
)

_SITECUSTOMIZE = """\
try:
    import errno
    import socket

    _orig_connect = socket.socket.connect
    _orig_create = socket.create_connection

    def _loopback(host):
        if isinstance(host, (bytes, bytearray)):
            try:
                host = host.decode("utf-8", "replace")
            except Exception:
                return False
        if not isinstance(host, str):
            return False
        h = host.strip().lower()
        if h.startswith("[") and h.endswith("]"):
            h = h[1:-1]
        return h in {"127.0.0.1", "::1", "localhost"}

    def _blocked():
        raise OSError(errno.ENETUNREACH, "claimidx-replay-offline")

    def _connect(self, address):
        if isinstance(address, (str, bytes, bytearray)):
            return _orig_connect(self, address)
        if isinstance(address, tuple) and address and _loopback(address[0]):
            return _orig_connect(self, address)
        _blocked()

    def _create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *, all_errors=False):
        if isinstance(address, (str, bytes, bytearray)):
            return _orig_create(address, timeout, source_address, all_errors=all_errors)
        host = address[0] if isinstance(address, tuple) and address else None
        if _loopback(host):
            return _orig_create(address, timeout, source_address, all_errors=all_errors)
        _blocked()

    socket.socket.connect = _connect
    socket.create_connection = _create_connection
except Exception:
    pass
"""

_BLOCK_NET_JS = """\
try {
  var net = require("net");
  var orig = net.Socket.prototype.connect;
  function loopback(h) {
    if (h === "") return true;
    var s = String(h).toLowerCase();
    return s === "127.0.0.1" || s === "::1" || s === "localhost";
  }
  function blocked() {
    var err = new Error("claimidx-replay-offline");
    err.code = "ENETUNREACH";
    err.errno = process.platform === "win32" ? 10051 : 101;
    throw err;
  }
  net.Socket.prototype.connect = function () {
    var args = Array.prototype.slice.call(arguments);
    var a0 = args[0];
    if (typeof a0 === "string" || (typeof Buffer !== "undefined" && Buffer.isBuffer(a0))) {
      return orig.apply(this, args);
    }
    if (typeof a0 === "number" || typeof a0 === "function" || a0 == null) {
      return orig.apply(this, args);
    }
    if (typeof a0 === "object") {
      if (a0.path) return orig.apply(this, args);
      var has = false;
      var host;
      if (Object.prototype.hasOwnProperty.call(a0, "host")) { host = a0.host; has = true; }
      else if (Object.prototype.hasOwnProperty.call(a0, "hostname")) { host = a0.hostname; has = true; }
      if (has && loopback(host)) return orig.apply(this, args);
      blocked();
    }
    blocked();
  };
} catch (e) {}
"""

_OFFLINE_DIR: str | None = None


def _py_isolation(parts: list[str]) -> bool:
    """True when python/python3 is started with -S, -I, or -P (sitecustomize will not load)."""
    if not parts or _norm_head(parts[0]) not in {"python", "python3"}:
        return False
    for tok in parts[1:]:
        if tok.startswith("--"):
            continue
        if not tok.startswith("-") or tok == "-":
            continue
        body = tok[1:]
        if body.startswith("c"):
            return False
        cut = body.split("c", 1)[0].replace("X", "")
        if any(ch in cut for ch in "SIP"):
            return True
        if "c" in body:
            return False
    return False


def _offline_dir() -> str:
    """Temp dir holding sitecustomize.py and block_net.js. Cached for the process."""
    global _OFFLINE_DIR
    if _OFFLINE_DIR and os.path.isdir(_OFFLINE_DIR):
        return _OFFLINE_DIR
    import tempfile

    root = tempfile.mkdtemp(prefix="claimidx-offline-")
    Path(root, "sitecustomize.py").write_text(_SITECUSTOMIZE, encoding="utf-8")
    Path(root, "block_net.js").write_text(_BLOCK_NET_JS, encoding="utf-8")
    _OFFLINE_DIR = root
    return root


def _bwrap_argv(argv: list[str], cwd: str | None) -> list[str]:
    work = os.path.abspath(cwd or os.getcwd())
    return [
        "bwrap",
        "--unshare-net",
        "--unshare-pid",
        "--die-with-parent",
        "--new-session",
        "--ro-bind",
        "/",
        "/",
        # A writable minimal /dev (/dev/null) and this pid namespace's /proc; a read-only host /dev breaks pytest capture.
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        # tmpfs before the work bind: a --cwd under /tmp (pytest tmp_path) must stay visible.
        "--tmpfs",
        "/tmp",
        "--bind",
        work,
        work,
        "--chdir",
        work,
        "--",
        *argv,
    ]


def _apply_offline(env: dict[str, str], parts: list[str], argv: list[str], *, net_open: bool, cwd: str | None) -> tuple[list[str], str, str]:
    """Overlay the child env. Returns (argv, net, net_how). Does not touch os.environ."""
    if net_open:
        return argv, "open", ""
    if not parts:
        return argv, "unenforced", ""
    head = _norm_head(parts[0])
    if head == "docker":
        return argv, "unenforced", "docker"
    if os.name != "nt" and shutil.which("bwrap"):
        return _bwrap_argv(argv, cwd), "offline", "bwrap"
    if head in {"python", "python3", "pytest"} and not _py_isolation(parts):
        root = _offline_dir()
        prev = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = root + (os.pathsep + prev if prev else "")
        return argv, "offline", "interpreter"
    if head in {"python", "python3"} and _py_isolation(parts):
        return argv, "unenforced", "python-flags"
    if head in {"node", "npm", "npx"}:
        js = str(Path(_offline_dir(), "block_net.js"))
        flag = "--require " + (f'"{js}"' if " " in js else js)
        existing = env.get("NODE_OPTIONS", "")
        if "block_net.js" not in existing:
            env["NODE_OPTIONS"] = (flag + " " + existing).strip()
        return argv, "offline", "interpreter"
    if head == "cargo":
        env["CARGO_NET_OFFLINE"] = "true"
        return argv, "unenforced", "cargo-net-offline"
    if head == "go":
        env["GOPROXY"] = "off"
        return argv, "unenforced", "go"
    if head == "uv":
        return argv, "unenforced", "uv"
    return argv, "unenforced", head


def _close_job(job: object) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = ctypes.c_int
        k32.CloseHandle(job)
    except Exception:
        return


def _job_kill_on_close(proc: subprocess.Popen) -> object | None:
    """Windows Job Object with KILL_ON_JOB_CLOSE. Not a network control. Failure is ignored."""
    if os.name != "nt":
        return None
    if sys.platform != "win32":
        return None
    job = None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.CreateJobObjectW.restype = ctypes.c_void_p
        k32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.SetInformationJobObject.restype = wintypes.BOOL
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        k32.CloseHandle.restype = wintypes.BOOL

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = 0x2000
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(job)
            return None
        handle = k32.OpenProcess(0x0100 | 0x0001, False, int(proc.pid))
        if not handle:
            k32.CloseHandle(job)
            return None
        ok = k32.AssignProcessToJobObject(job, handle)
        k32.CloseHandle(handle)
        if not ok:
            k32.CloseHandle(job)
            return None
        return job
    except Exception:
        if job:
            _close_job(job)
        return None


def _run_captured(argv: list[str], timeout: float, cwd: str | None, env: dict[str, str]) -> subprocess.CompletedProcess:
    """Popen + communicate. On Windows the child is in a kill-on-close job until communicate returns."""
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=cwd or None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    job = _job_kill_on_close(proc)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.communicate(timeout=5)
            except Exception:
                pass
            raise
        return subprocess.CompletedProcess(argv, proc.returncode, out or "", err or "")
    finally:
        if job is not None:
            _close_job(job)


def network_blocked(result: ReplayResult) -> bool:
    """True when an offline replay actually blocked a network attempt. A hold is never a block."""
    if result.held or (result.net or "") != "offline":
        return False
    blob = "\n".join([result.stdout or "", result.stderr or "", result.reason or ""])
    return any(sig in blob for sig in _BLOCK_SIGNALS)


def replay(cmd: str, expect: int = 0, timeout: float = 45.0, cwd: str | None = None, *, trust: str = "local", net: bool = False) -> ReplayResult:
    """Run eval.cmd. `trust="untrusted"` (a pulled or seed claim) only runs the portable proof grammar.

    Default is offline. `net=True` leaves the network open and records net=open; it does not mint nr.
    """
    if not (cmd or "").strip():
        return ReplayResult(True, False, None, expect, False, "builtin")
    ok, reason = eval_allowed(cmd)
    if not ok:
        return ReplayResult(False, False, None, expect, False, reason)
    if trust != "local":
        from .evaltrust import untrusted_reason

        why = untrusted_reason(cmd, cwd)
        if why:
            return ReplayResult(True, False, None, expect, False, f"eval-untrusted: {why}")
    try:
        extra_env, parts = split_eval(cmd)
    except ValueError as e:
        return ReplayResult(False, False, None, expect, False, f"unparseable eval: {e}")
    if parts and _norm_head(parts[0]) in ("true", "false"):
        rc = 0 if _norm_head(parts[0]) == "true" else 1
        held = rc == expect
        return ReplayResult(True, True, rc, expect, held, "builtin")
    from .public import eval_is_proof

    if not eval_is_proof(cmd):
        return ReplayResult(True, False, None, expect, False, "builtin")
    missing = _precondition(_norm_head(parts[0]), cwd, cmd)
    if missing:
        return ReplayResult(True, False, None, expect, False, missing)
    argv = resolve_argv(parts, cwd)
    observed = observe_env(argv)
    env = os.environ.copy()
    env.update(extra_env)
    argv, net_state, net_how = _apply_offline(env, parts, argv, net_open=bool(net), cwd=cwd)
    t0 = time.monotonic()
    try:
        # Decode as UTF-8, not locale.getpreferredencoding() (cp1252 on Windows): stderr feeds fingerprints,
        # and a mis-decoded `café` would give an OS-dependent fp.
        proc = _run_captured(argv, timeout, cwd, env)
    except subprocess.TimeoutExpired:
        ms = int((time.monotonic() - t0) * 1000)
        return ReplayResult(True, True, 124, expect, False, "timeout", env=observed, ms=ms, net=net_state, net_how=net_how)
    except OSError as e:
        ms = int((time.monotonic() - t0) * 1000)
        return ReplayResult(True, False, None, expect, False, f"exec-error:{e}", env=observed, ms=ms, net=net_state, net_how=net_how)
    held = proc.returncode == expect
    ms = int((time.monotonic() - t0) * 1000)
    return ReplayResult(
        True,
        True,
        proc.returncode,
        expect,
        held,
        "held" if held else "eval-miss",
        proc.stdout or "",
        proc.stderr or "",
        observed,
        ms,
        net_state,
        net_how,
    )
