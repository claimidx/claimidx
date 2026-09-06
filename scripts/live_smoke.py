"""Live smoke: the whole loop per ecosystem, with real toolchains and a fresh clone.

    python scripts/live_smoke.py                 # every ecosystem whose toolchain is on PATH
    python scripts/live_smoke.py go rust         # a subset
    python scripts/live_smoke.py --keep          # leave the trees on disk

For each ecosystem: make a tiny git project that fails to build for a missing
dependency, run the build through `claimidx run` (verdict: solve), fix it by
hand, run again (nudge), `claimidx claim --yes` (drafted pin or diff, proven
in a clean clone), then clone the broken commit and `claimidx apply` the
claim there (build passes, hold recorded, no nudge afterwards).

Runs against a scratch index (`--scratch`): nothing is shared or remembered
outside the smoke. Exit status is the number of ecosystems that failed.
The unit suite did not predict the failures this caught; keep it in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PY = sys.executable


def sh(argv: list[str], cwd: Path, *, env: dict | None = None, check: bool = True, timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(argv, cwd=str(cwd), capture_output=True, text=True, env=env, timeout=timeout, check=False)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} -> rc {proc.returncode}\n{proc.stdout[-800:]}\n{proc.stderr[-800:]}")
    return proc


def cix(args: list[str], cwd: Path, env: dict, *, check: bool = False) -> subprocess.CompletedProcess:
    return sh([PY, "-m", "claimidx", "--scratch", *args], cwd, env=env, check=check)


def git_init(tree: Path, files: dict[str, str]) -> None:
    tree.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8", newline="\n")
    sh(["git", "init", "-q"], tree)
    sh(["git", "-c", "user.name=smoke", "-c", "user.email=s@s", "add", "-A"], tree)
    sh(["git", "-c", "user.name=smoke", "-c", "user.email=s@s", "commit", "-qm", "broken"], tree)


ECOS: dict[str, dict] = {
    "py": {
        "tool": "python",
        "files": {
            "app.py": "import tomli\nprint(tomli.loads('a = 1'))\n",
            ".gitignore": ".venv/\n",
            "pyproject.toml": "[project]\nname = 'tiny'\nversion = '0'\n",
        },
        "setup": lambda tree: sh([PY, "-m", "venv", str(tree / ".venv")], tree),
        "build": lambda tree: [str(tree / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), "app.py"],
        "fix": lambda tree: sh(
            [str(tree / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")), "-m", "pip", "install", "-q", "tomli"], tree
        ),
        "claim_extra": [],
    },
    "go": {
        "tool": "go",
        "files": {
            "go.mod": "module example.com/tiny\n\ngo 1.22\n",
            "main.go": 'package main\n\nimport (\n\t"fmt"\n\n\t"github.com/google/uuid"\n)\n\nfunc main() { fmt.Println(uuid.NewString()) }\n',
        },
        "setup": lambda tree: None,
        "build": lambda tree: ["go", "build", "./..."],
        "fix": lambda tree: sh(["go", "get", "github.com/google/uuid@v1.6.0"], tree),
        "claim_extra": [],
    },
    "rust": {
        "tool": "cargo",
        "files": {
            "Cargo.toml": '[package]\nname = "tiny"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n',
            "src/main.rs": 'fn main() { println!("{}", uuid::Uuid::nil()); }\n',
            ".gitignore": "target/\n",
        },
        "setup": lambda tree: None,
        "build": lambda tree: ["cargo", "check", "-q"],
        "fix": lambda tree: sh(["cargo", "add", "-q", "uuid@1"], tree),
        "claim_extra": [],
    },
    "gradle": {
        "tool": "gradle",
        "files": {
            "settings.gradle.kts": 'rootProject.name = "tiny"\n',
            "build.gradle.kts": "plugins {\n    java\n}\n\nrepositories {\n    mavenCentral()\n}\n",
            "src/main/java/App.java": 'import org.apache.commons.lang3.StringUtils;\n\npublic class App {\n    public static void main(String[] a) { System.out.println(StringUtils.capitalize("hi")); }\n}\n',
            ".gitignore": "build/\n.gradle/\n",
        },
        "setup": lambda tree: None,
        "build": lambda tree: ["gradle", "-q", "compileJava"],
        "fix": lambda tree: (tree / "build.gradle.kts").write_text(
            (tree / "build.gradle.kts").read_text(encoding="utf-8") + '\ndependencies {\n    implementation("org.apache.commons:commons-lang3:3.17.0")\n}\n',
            encoding="utf-8",
            newline="\n",
        ),
        "claim_extra": ["--fix", "org.apache.commons:commons-lang3:3.17.0"],
    },
    "mvn": {
        "tool": "mvn",
        "files": {
            "pom.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0">\n  <modelVersion>4.0.0</modelVersion>\n'
                "  <groupId>example</groupId>\n  <artifactId>tiny</artifactId>\n  <version>1.0</version>\n  <properties>\n"
                "    <maven.compiler.release>${java.specification.version}</maven.compiler.release>\n    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>\n  </properties>\n</project>\n"
            ),
            "src/main/java/App.java": 'import org.apache.commons.lang3.StringUtils;\n\npublic class App {\n    public static void main(String[] a) { System.out.println(StringUtils.capitalize("hi")); }\n}\n',
            ".gitignore": "target/\n",
        },
        "setup": lambda tree: None,
        "build": lambda tree: ["mvn", "-q", "compile"],
        "fix": lambda tree: __import__("claimidx.apply", fromlist=["write_java_dependency"]).write_java_dependency(
            str(tree), "pom.xml", "org.apache.commons:commons-lang3:3.17.0"
        ),
        "claim_extra": [],
    },
}


def _remembered(env: dict) -> dict:
    try:
        return json.loads(Path(env["CLAIMIDX_LAST_FAILURE"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return {}


def run_eco(name: str, spec: dict, root: Path, env: dict) -> list[str]:
    problems: list[str] = []
    tree = root / name
    git_init(tree, spec["files"])
    spec["setup"](tree)
    build = spec["build"](tree)

    r = cix(["run", "--"] + build, tree, env)
    if r.returncode == 0 or "CLAIMIDX verdict" not in r.stderr:
        problems.append(f"broken build should fail with a verdict: rc={r.returncode} stderr={r.stderr[-300:]}")
    spec["fix"](tree)
    r = cix(["run", "--"] + build, tree, env)
    if r.returncode != 0 or "CLAIMIDX fixed" not in r.stderr:
        problems.append(f"fixed build should pass with a nudge: rc={r.returncode} stderr={r.stderr[-300:]}")
    r = cix(["--fmt", "json", "claim", "--yes", *spec["claim_extra"]], tree, env)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    cid = out.get("id")
    if not cid or not (out.get("replay") or {}).get("recorded"):
        problems.append(f"claim --yes should record: {r.stdout[-400:]} {r.stderr[-300:]}")
        return problems
    room = out.get("clean_room") or {}
    if room and room.get("ran") and not room.get("recorded"):
        problems.append(f"clean room ran but did not record: {room.get('reason')}")
    clone = root / f"{name}-clone"
    sh(["git", "clone", "-q", str(tree), str(clone)], root)
    if name == "py":
        spec["setup"](clone)
    r = cix(["run", "--"] + spec["build"](clone), clone, env)
    if r.returncode == 0 or f"apply {cid}" not in r.stderr:
        verdict = next((ln for ln in r.stderr.splitlines() if ln.startswith("CLAIMIDX verdict")), r.stderr[-300:])
        rec = _remembered(env)
        shown = cix(["--fmt", "json", "show", cid], clone, env)
        try:
            row = json.loads(shown.stdout)
            stored = {k: row.get(k) for k in ("fp", "rt", "eco", "err")}
        except ValueError:
            stored = shown.stdout[-200:]
        query = {k: rec.get(k) for k in ("fp", "rt", "eco", "err")}
        problems.append(f"clone should fail with an apply verdict for {cid}: {verdict} | query: {query} | stored: {stored}")
    r = cix(["apply", cid, "--cwd", str(clone), "--yes"], clone, env)
    if r.returncode != 0 or "applied and held" not in r.stdout:
        problems.append(f"apply should hold: rc={r.returncode} {r.stdout[-300:]} {r.stderr[-300:]}")
    r = cix(["run", "--"] + spec["build"](clone), clone, env)
    if r.returncode != 0 or "CLAIMIDX" in r.stderr:
        problems.append(f"build after apply should pass quietly: rc={r.returncode} stderr={r.stderr[-300:]}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ecos", nargs="*", help=f"subset of {', '.join(ECOS)}")
    ap.add_argument("--keep", action="store_true")
    ns = ap.parse_args()
    wanted = ns.ecos or list(ECOS)
    root = Path(tempfile.mkdtemp(prefix="cix-smoke-"))
    env = dict(os.environ)
    env["CLAIMIDX_SCRATCH_DIR"] = str(root / "scratch")
    env["CLAIMIDX_LAST_FAILURE"] = str(root / "scratch" / "last_failure.json")
    failed = 0
    for name in wanted:
        spec = ECOS[name]
        if not shutil.which(spec["tool"]):
            print(f"{name}: skipped ({spec['tool']} not on PATH)")
            continue
        try:
            problems = run_eco(name, spec, root, env)
        except Exception as e:  # noqa: BLE001 - a smoke reports, it does not hide
            problems = [f"exception: {e}"]
        if problems:
            failed += 1
            print(f"{name}: FAIL")
            for p in problems:
                print("  - " + p.replace("\n", "\n    "))
        else:
            print(f"{name}: ok")
    if ns.keep:
        print(f"trees kept under {root}")
    else:
        shutil.rmtree(root, ignore_errors=True)
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
