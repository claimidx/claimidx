"""Two trust tiers for eval.cmd.

The allowlist in `policy.py` says what an eval may look like. It is a denylist
of tokens inside `python -c` / `node -e`, and a denylist is a speed bump:
`shutil.rmtree`, `importlib.import_module('o'+'s')`, `require('child_process')`,
`npx <anything-on-npm>`, and `go run pkg@latest` all pass it. That is fine for
an eval the agent wrote itself and is about to run in its own tree. It is not
fine for an eval that arrived in a pulled ledger row: replaying it is running
someone else's code.

So `confirm --replay` and `verify` ask *where the eval came from*:

- **local**: the claim was published on this machine (a `publish` event exists
  for its id). The wide policy applies.
- **untrusted**: everything else — pulled from a home, seed corpus, imported
  jsonl. The eval must fit the portable proof grammar below or it is skipped
  with `eval-untrusted: <why>` and never mints anything. `--trust-eval` runs
  it anyway, after printing exactly what will run.

The grammar is what `refine_eval` and `claim` generate, plus the usual build
and test recipes that only run the agent's own tree:

  python -c  : imports, version checks, asserts, raise SystemExit(...) — an AST
               subset with no attribute calls except importlib.metadata.version
  node -e    : require("pkg") and the package.json version check shape
  python/node <file>   : a relative file that already exists under cwd
  pytest / python -m pytest|unittest|compileall|py_compile
  npx <bin>  : only when node_modules/.bin/<bin> already exists (no download)
  npm test|run|ls ; node --check
  go build|vet|test|list ./... (no module@version arguments)
  cargo check|build|test|clippy|run ; rustc <file>
  uv run pytest|python …  (recursively checked)
  docker build … ; docker compose config|build
  true / false / test
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

from .policy import _norm_head, split_eval

_PY_CALL_ALLOW = {
    "print",
    "len",
    "str",
    "int",
    "tuple",
    "bool",
    "repr",
    "hasattr",
    "version",
    "SystemExit",
    "exit",
    "sys.exit",
    "importlib.metadata.version",
    "metadata.version",
    "importlib.import_module",  # argument must be a constant; checked below
    "importlib.util.find_spec",
    "sys.version_info",
    "platform.python_version",
    "platform.python_version_tuple",
    "re.search",
    "re.match",
    "re.fullmatch",
    "re.sub",
    "re.compile",
}
_RE_CALLS = {"re.search", "re.match", "re.fullmatch", "re.sub", "re.compile"}
_PY_ATTR_DUNDER_OK = {"__version__", "__file__", "__name__", "__spec__"}
_PY_STR_METHODS = {"split", "strip", "lower", "upper", "startswith", "endswith", "replace", "join", "partition", "rsplit"}
_NODE_REQUIRE = r"""require\((['"])(@?[A-Za-z0-9_./-]+)\1\)"""
_NODE_SAFE = [
    re.compile(rf"^{_NODE_REQUIRE}\s*;?$"),
    re.compile(rf"^{_NODE_REQUIRE}\.version\s*;?$"),
    re.compile(rf"""^if\s*\(\s*{_NODE_REQUIRE}\.version\s*!==?\s*(['"])[^'"]{{1,40}}\3\s*\)\s*process\.exit\(1\)\s*;?$"""),
    re.compile(rf"""^const\s+\w+\s*=\s*{_NODE_REQUIRE}\s*;?\s*(?:if\s*\(\s*\w+\.version\s*!==?\s*(['"])[^'"]{{1,40}}\3\s*\)\s*process\.exit\(1\)\s*;?)?$"""),
]
_IMPORTABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_REL_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")
_GO_MODULE_AT = re.compile(r"@")


class Untrusted(Exception):
    """The eval does not fit the portable grammar; carries the reason."""


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else ""
    return ""


def _check_py_expr(node: ast.AST) -> None:
    if isinstance(node, ast.Constant):
        return
    if isinstance(node, ast.Name):
        if node.id.startswith("__") and node.id not in _PY_ATTR_DUNDER_OK:
            raise Untrusted(f"python -c uses {node.id}")
        return
    if isinstance(node, ast.Attribute):
        if node.attr.startswith("__") and node.attr not in _PY_ATTR_DUNDER_OK:
            raise Untrusted(f"python -c reads {node.attr}")
        _check_py_expr(node.value)
        return
    if isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            _check_py_expr(e)
        return
    if isinstance(node, ast.Compare):
        _check_py_expr(node.left)
        for c in node.comparators:
            _check_py_expr(c)
        return
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            _check_py_expr(v)
        return
    if isinstance(node, ast.UnaryOp):
        _check_py_expr(node.operand)
        return
    if isinstance(node, ast.BinOp):
        _check_py_expr(node.left)
        _check_py_expr(node.right)
        return
    if isinstance(node, ast.IfExp):
        _check_py_expr(node.test)
        _check_py_expr(node.body)
        _check_py_expr(node.orelse)
        return
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        _check_py_expr(node.elt)
        for gen in node.generators:
            if not isinstance(gen.target, (ast.Name, ast.Tuple)):
                raise Untrusted("python -c comprehension with a complex target")
            _check_py_expr(gen.iter)
            for cond in gen.ifs:
                _check_py_expr(cond)
        return
    if isinstance(node, ast.Subscript):
        _check_py_expr(node.value)
        _check_py_expr(node.slice)
        return
    if isinstance(node, ast.Slice):
        for part in (node.lower, node.upper, node.step):
            if part is not None:
                _check_py_expr(part)
        return
    if isinstance(node, ast.JoinedStr):
        for v in node.values:
            _check_py_expr(v.value if isinstance(v, ast.FormattedValue) else v)
        return
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        if name not in _PY_CALL_ALLOW and not (
            isinstance(node.func, ast.Attribute)
            and (node.func.attr in _PY_STR_METHODS or (node.func.attr in {"version", "find_spec"} and isinstance(node.func.value, ast.Name)))
        ):
            raise Untrusted(f"python -c calls {name or type(node.func).__name__}()")
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"version", "find_spec"}:
            name = "version"
        if node.keywords:
            raise Untrusted(f"python -c passes keywords to {name}()")
        for a in node.args:
            if name in _RE_CALLS and a is node.args[0] and not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                raise Untrusted(f"python -c calls {name}() with a non-literal pattern")
            if name in {"importlib.import_module", "version", "importlib.metadata.version", "metadata.version", "importlib.util.find_spec"}:
                if not isinstance(a, ast.Constant) or not isinstance(a.value, str):
                    raise Untrusted(f"python -c calls {name}() with a non-literal")
                if name == "importlib.import_module" and not _IMPORTABLE.match(a.value):
                    raise Untrusted("python -c import_module() with a non-module name")
            _check_py_expr(a)
        return
    raise Untrusted(f"python -c uses {type(node).__name__}")


def _check_py_stmt(node: ast.stmt) -> None:
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return
    if isinstance(node, ast.Pass):
        return
    if isinstance(node, ast.Expr):
        _check_py_expr(node.value)
        return
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if not isinstance(t, ast.Name):
                raise Untrusted("python -c assigns to a non-name")
        _check_py_expr(node.value)
        return
    if isinstance(node, ast.Assert):
        _check_py_expr(node.test)
        if node.msg is not None:
            _check_py_expr(node.msg)
        return
    if isinstance(node, ast.Raise):
        if node.exc is not None:
            _check_py_expr(node.exc)
        return
    if isinstance(node, ast.If):
        _check_py_expr(node.test)
        for s in node.body + node.orelse:
            _check_py_stmt(s)
        return
    raise Untrusted(f"python -c uses {type(node).__name__}")


def check_python_c(code: str) -> None:
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        raise Untrusted(f"python -c does not parse: {e.msg}") from e
    for stmt in tree.body:
        _check_py_stmt(stmt)


def _rel_file_exists(token: str, cwd: str | None) -> bool:
    if not _REL_FILE.match(token) or ".." in token.split("/"):
        return False
    return (Path(cwd or os.getcwd()) / token).is_file()


def _check_python(parts: list[str], cwd: str | None) -> None:
    args = parts[1:]
    if not args:
        raise Untrusted("bare python opens a REPL")
    if args[0] == "-c":
        if len(args) < 2:
            raise Untrusted("python -c without code")
        check_python_c(args[1])
        if len(args) > 2:
            raise Untrusted("python -c with extra arguments")
        return
    if args[0] == "-m":
        mod = args[1] if len(args) > 1 else ""
        if mod in {"pytest", "unittest", "compileall", "py_compile"}:
            _check_pytest_args(args[2:]) if mod == "pytest" else None
            return
        raise Untrusted(f"python -m {mod or '?'} is not a check")
    if args[0].startswith("-"):
        raise Untrusted(f"python {args[0]} is not a check")
    if not _rel_file_exists(args[0], cwd):
        raise Untrusted(f"python {args[0]}: not an existing file under cwd")


def _check_pytest_args(args: list[str]) -> None:
    for a in args:
        if a in {"-p", "--pyargs", "-c", "--rootdir", "--override-ini", "-o"} or a.startswith(("-p", "--pyargs=", "-c=", "--override-ini=", "-o=")):
            raise Untrusted(f"pytest {a} loads code outside the tree")


def _check_node(parts: list[str], cwd: str | None) -> None:
    args = parts[1:]
    if not args:
        raise Untrusted("bare node opens a REPL")
    if args[0] in {"-e", "--eval", "-p", "--print"}:
        code = " ".join(args[1:]).strip()
        if not any(p.match(code) for p in _NODE_SAFE):
            raise Untrusted("node -e is not a require()/version check")
        return
    if args[0] in {"--check", "-c"}:
        if len(args) == 2 and _rel_file_exists(args[1], cwd):
            return
        raise Untrusted("node --check needs one existing file")
    if args[0].startswith("-"):
        raise Untrusted(f"node {args[0]} is not a check")
    if not _rel_file_exists(args[0], cwd):
        raise Untrusted(f"node {args[0]}: not an existing file under cwd")


def _check_npx(parts: list[str], cwd: str | None) -> None:
    args = [a for a in parts[1:] if not a.startswith("-")]
    if not args:
        raise Untrusted("npx without a binary")
    binary = args[0]
    root = Path(cwd or os.getcwd())
    if not _REL_FILE.match(binary) or not (root / "node_modules" / ".bin" / binary).exists():
        raise Untrusted(f"npx {binary} would download and run a package")


def _check_npm(parts: list[str]) -> None:
    sub = parts[1] if len(parts) > 1 else ""
    if sub in {"test", "t", "ls", "audit", "run", "run-script"}:
        return
    raise Untrusted(f"npm {sub or '?'} is not a check")


def _check_go(parts: list[str]) -> None:
    sub = parts[1] if len(parts) > 1 else ""
    if sub not in {"build", "vet", "test", "list"} and not (sub == "mod" and parts[2:3] == ["verify"]):
        raise Untrusted(f"go {sub or '?'} is not a check")
    for a in parts[2:]:
        if _GO_MODULE_AT.search(a):
            raise Untrusted(f"go {sub} {a} fetches a module")


def _check_cargo(parts: list[str]) -> None:
    sub = parts[1] if len(parts) > 1 else ""
    if sub not in {"check", "build", "test", "clippy", "run", "fmt"}:
        raise Untrusted(f"cargo {sub or '?'} is not a check")


def _check_docker(parts: list[str]) -> None:
    sub = parts[1] if len(parts) > 1 else ""
    if sub == "build":
        return
    if sub == "compose" and parts[2:3] and parts[2] in {"config", "build"}:
        return
    raise Untrusted(f"docker {sub or '?'} runs a container")


def _check_uv(parts: list[str], cwd: str | None) -> None:
    if parts[1:2] != ["run"]:
        raise Untrusted(f"uv {parts[1] if len(parts) > 1 else '?'} is not a check")
    rest = parts[2:]
    while rest and rest[0].startswith("-"):
        if rest[0] in {"--with", "-w", "--with-requirements", "--script"} or rest[0].startswith("--with"):
            raise Untrusted("uv run --with installs a package")
        rest = rest[1:]
    if not rest:
        raise Untrusted("uv run without a command")
    check_untrusted(" ".join(_quote(t) for t in rest), cwd)


def _quote(token: str) -> str:
    import shlex

    return shlex.quote(token)


def check_untrusted(cmd: str, cwd: str | None = None) -> None:
    """Raise Untrusted unless `cmd` fits the portable proof grammar."""
    try:
        _env, parts = split_eval(cmd)
    except ValueError as e:
        raise Untrusted(f"unparseable eval: {e}") from e
    if not parts:
        raise Untrusted("empty eval")
    head = _norm_head(parts[0])
    if head in {"true", "false", "test"}:
        return
    if head in {"python", "python3"}:
        return _check_python(parts, cwd)
    if head == "pytest":
        return _check_pytest_args(parts[1:])
    if head == "node":
        return _check_node(parts, cwd)
    if head == "npx":
        return _check_npx(parts, cwd)
    if head == "npm":
        return _check_npm(parts)
    if head == "go":
        return _check_go(parts)
    if head == "cargo":
        return _check_cargo(parts)
    if head == "rustc":
        files = [a for a in parts[1:] if not a.startswith("-")]
        if len(files) == 1 and _rel_file_exists(files[0], cwd):
            return
        raise Untrusted("rustc needs one existing file")
    if head == "docker":
        return _check_docker(parts)
    if head == "uv":
        return _check_uv(parts, cwd)
    raise Untrusted(f"{head} is not in the portable proof grammar")


def untrusted_reason(cmd: str, cwd: str | None = None) -> str:
    """ "" when the eval fits the grammar, else the reason it does not."""
    try:
        check_untrusted(cmd, cwd)
    except Untrusted as e:
        return str(e)
    return ""


def eval_trust(store, claim, *, override: bool = False) -> str:
    """'local' when the claim was published on this machine (or override), else 'untrusted'."""
    if override:
        return "local"
    if getattr(claim, "src", "local") != "local":
        return "untrusted"
    try:
        return "local" if store.has_event(claim.id, ("publish",)) else "untrusted"
    except Exception:
        return "untrusted"
