"""`claimidx apply <id>`: from a verdict to a recorded hold in one command.

The verdict says "apply fix.b, then confirm --replay". For the two remedy
kinds that are mechanical — a dependency pin and a `diff --git` patch —
this does exactly that and nothing more: install the spec with the tree's
own package manager, or `git apply` the diff, then replay the eval under
--cwd and record the result through the same gate as `confirm --replay`.

The tree's own package manager, per ecosystem:

  py    <cwd>/.venv python -m pip install <spec>
  npm   npm install <spec>
  go    go get <module>[@version]
  rust  cargo add <crate>[@version]
  java  the coordinate `group:artifact:version` is written into pom.xml or
        build.gradle(.kts) — Maven and Gradle have no "add" command — and the
        eval's build resolves it

It is not an executor. `cmd`, `config`, and prose remedies are printed for
the agent to apply by hand. Without --yes it only prints the plan; a claim
that was not published here is named as such, because installing its pin
is running someone else's choice of package.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any

from .models import Claim

_PY_SPEC = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9_,.-]+\])?(?:(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9_.+*!-]+(?:,(?:==|>=|<=|~=|!=|<|>)[A-Za-z0-9_.+*!-]+)*)?$"
)
_NPM_SPEC = re.compile(r"^(?:@[A-Za-z0-9_.-]+/)?[A-Za-z0-9][A-Za-z0-9._-]*(?:@[A-Za-z0-9_.^~<>=|* -]+)?$")
# A module path has a host and at least one path element; no scheme, no flags, no spaces.
_GO_SPEC = re.compile(r"^[a-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_.~-]+)+(?:@v?[0-9A-Za-z.+-]+)?$")
_CARGO_SPEC = re.compile(r"^[A-Za-z0-9_-]+(?:@[0-9A-Za-z.^~<>=*,+-]+)?$")
_JAVA_SPEC = re.compile(r"^([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+):([0-9A-Za-z.+-]+)$")


def _pin_spec(fix_b: str) -> str:
    from .public import _pin_line

    return _pin_line(fix_b)


def _java_build_file(root: str) -> str | None:
    """The build file a coordinate goes into: pom.xml, else build.gradle.kts, else build.gradle."""
    for name in ("pom.xml", "build.gradle.kts", "build.gradle"):
        if os.path.isfile(os.path.join(root, name)):
            return name
    return None


def plan(claim: Claim, cwd: str) -> dict[str, Any]:
    """What `apply` would run. `steps` is a list of argv; `edit` a build-file write; `manual` is set when the agent must act."""
    root = os.path.abspath(cwd or os.getcwd())
    kind = claim.fix.k
    body = claim.fix.b or ""
    eco = (claim.eco or "").lower()
    out: dict[str, Any] = {"id": claim.id, "kind": kind, "cwd": root, "steps": [], "stdin": None}
    if kind in {"pin", "constraint"}:
        spec = _pin_spec(body)
        if eco == "go":
            if not _GO_SPEC.match(spec):
                out["manual"] = f"not a plain Go module spec (module/path[@version] only; no flags or URLs); apply by hand: {body[:200]}"
                return out
            out["steps"] = [["go", "get", spec]]
            return out
        if eco == "rust":
            if not _CARGO_SPEC.match(spec):
                out["manual"] = f"not a plain crate spec (crate[@version] only; features and git sources are applied by hand): {body[:200]}"
                return out
            out["steps"] = [["cargo", "add", spec]]
            return out
        if eco == "java":
            m = _JAVA_SPEC.match(spec)
            if not m:
                out["manual"] = f"a Java pin needs group:artifact:version to write into the build file; apply by hand: {body[:200]}"
                return out
            build = _java_build_file(root)
            if not build:
                out["manual"] = f"no pom.xml or build.gradle under {root}; add {spec} to the build file your eval runs, then confirm --replay"
                return out
            out["edit"] = {"file": build, "coordinate": spec}
            return out
        if eco in {"npm", "node"} or spec.startswith("@"):
            if not _NPM_SPEC.match(spec):
                out["manual"] = f"not a plain npm spec; apply by hand: {body[:200]}"
                return out
            out["steps"] = [["npm", "install", spec]]
            return out
        if not _PY_SPEC.match(spec):
            out["manual"] = f"not a plain pip spec (URLs, -e, --index-url are never installed by apply); apply by hand: {body[:200]}"
            return out
        from .sandbox import project_python

        py = project_python(root)
        if not py:
            out["manual"] = f"no .venv under {root}; install {spec} into the environment your eval runs in, then confirm --replay"
            return out
        out["steps"] = [[py, "-m", "pip", "install", "--disable-pip-version-check", "-q", spec]]
        return out
    if kind == "patch":
        if not body.lstrip().startswith("diff --git"):
            out["manual"] = "fix.b is a description, not a diff; make the change by hand, then confirm --replay"
            return out
        out["steps"] = [["git", "apply", "--check", "-"], ["git", "apply", "-"]]
        out["stdin"] = body if body.endswith("\n") else body + "\n"
        return out
    if kind == "cmd":
        out["manual"] = f"cmd remedies are never executed by Claimidx; read and run it yourself if you agree with it: {body[:200]}"
        return out
    if kind == "wontfix":
        out["manual"] = "wontfix: there is nothing to apply; see claimidx alternatives"
        return out
    out["manual"] = f"{kind} remedy; apply by hand: {body[:200]}"
    return out


def render_plan(p: dict[str, Any], *, trusted: bool, own: str, src: str) -> str:
    lines = [f"# apply {p['id']} ({p['kind']}) in {p['cwd']}"]
    if not trusted:
        lines.append(f"# this claim was not published here (own={own}, src={src}); its remedy is another agent's choice")
    if p.get("manual"):
        lines.append("manual: " + p["manual"])
        return "\n".join(lines)
    if p.get("edit"):
        lines.append(f"edit: {p['edit']['file']} <- {p['edit']['coordinate']}   (dependency block; version replaced when the artifact is already there)")
    for step in p["steps"]:
        lines.append("$ " + shlex.join(step) + (" < fix.b" if p.get("stdin") and step[-1] == "-" else ""))
    lines.append("then: confirm --replay --cwd " + p["cwd"])
    return "\n".join(lines)


# --- Java build files ---------------------------------------------------------------------------

_POM_DEP = re.compile(r"(<dependency>)(.*?)(</dependency>)", re.S)
_POM_MGMT = re.compile(r"<dependencyManagement>.*?</dependencyManagement>", re.S)


def _pom_add(text: str, group: str, artifact: str, version: str) -> str:
    """Set the version of an existing `<dependency>`, else append one to the project's `<dependencies>`."""

    def _has(block: str, tag: str, value: str) -> bool:
        return re.search(rf"<{tag}>\s*{re.escape(value)}\s*</{tag}>", block) is not None

    def _bump(m: re.Match[str]) -> str:
        inner = m.group(2)
        if not (_has(inner, "groupId", group) and _has(inner, "artifactId", artifact)):
            return m.group(0)
        if re.search(r"<version>.*?</version>", inner, re.S):
            inner = re.sub(r"<version>.*?</version>", f"<version>{version}</version>", inner, count=1, flags=re.S)
        else:
            inner = re.sub(r"(<artifactId>.*?</artifactId>)", rf"\1\n      <version>{version}</version>", inner, count=1, flags=re.S)
        return m.group(1) + inner + m.group(3)

    found = False
    for m in _POM_DEP.finditer(text):
        if _has(m.group(2), "groupId", group) and _has(m.group(2), "artifactId", artifact):
            found = True
            break
    if found:
        return _POM_DEP.sub(_bump, text)
    managed = [(m.start(), m.end()) for m in _POM_MGMT.finditer(text)]
    close = None
    for m in re.finditer(r"([ \t]*)</dependencies>", text):
        if not any(s <= m.start() < e for s, e in managed):
            close = m
            break
    if close is not None:
        ind = close.group(1)
        block = (
            f"{ind}  <dependency>\n{ind}    <groupId>{group}</groupId>\n{ind}    <artifactId>{artifact}</artifactId>\n"
            f"{ind}    <version>{version}</version>\n{ind}  </dependency>\n"
        )
        return text[: close.start()] + block + text[close.start() :]
    end = re.search(r"([ \t]*)</project>", text)
    if end is None:
        raise ValueError("pom.xml has no </project>")
    ind = end.group(1)
    block = (
        f"{ind}  <dependencies>\n{ind}    <dependency>\n{ind}      <groupId>{group}</groupId>\n{ind}      <artifactId>{artifact}</artifactId>\n"
        f"{ind}      <version>{version}</version>\n{ind}    </dependency>\n{ind}  </dependencies>\n"
    )
    return text[: end.start()] + block + text[end.start() :]


def _gradle_add(text: str, group: str, artifact: str, version: str, *, kts: bool) -> str:
    """Replace the version where the coordinate already appears, else add an `implementation` line to `dependencies {}`."""
    coord = f"{group}:{artifact}"
    pat = re.compile(r"(['\"])" + re.escape(coord) + r":[^'\"\s]+(['\"])")
    if pat.search(text):
        return pat.sub(lambda m: f"{m.group(1)}{coord}:{version}{m.group(2)}", text, count=1)
    line = f'    implementation("{coord}:{version}")' if kts else f"    implementation '{coord}:{version}'"
    lines = text.splitlines(keepends=True)
    start = next((i for i, ln in enumerate(lines) if re.match(r"^\s*dependencies\s*\{", ln)), None)
    if start is None:
        sep = "" if not text or text.endswith("\n") else "\n"
        return text + sep + "\ndependencies {\n" + line + "\n}\n"
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth == 0:
            lines.insert(i, line + "\n")
            return "".join(lines)
    raise ValueError("dependencies block is not closed")


def write_java_dependency(root: str, build: str, coordinate: str) -> None:
    m = _JAVA_SPEC.match(coordinate)
    if not m:
        raise ValueError(f"not group:artifact:version: {coordinate}")
    group, artifact, version = m.groups()
    path = os.path.join(root, build)
    with open(path, "rb") as fh:
        raw = fh.read()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8").replace("\r\n", "\n")
    if build == "pom.xml":
        out = _pom_add(text, group, artifact, version)
    else:
        out = _gradle_add(text, group, artifact, version, kts=build.endswith(".kts"))
    if crlf:
        out = out.replace("\n", "\r\n")  # keep the file's own line endings
    with open(path, "wb") as fh:
        fh.write(out.encode("utf-8"))


def run_plan(p: dict[str, Any]) -> dict[str, Any]:
    """Execute the plan's edit and steps in order; stop at the first failure."""
    done: list[dict[str, Any]] = []
    edit = p.get("edit")
    if edit:
        try:
            write_java_dependency(p["cwd"], edit["file"], edit["coordinate"])
        except (OSError, ValueError) as e:
            done.append({"argv": ["edit", edit["file"]], "rc": None, "error": str(e)})
            return {"ok": False, "steps": done}
        done.append({"argv": ["edit", edit["file"], edit["coordinate"]], "rc": 0, "stderr": "", "stdout": ""})
    for step in p["steps"]:
        try:
            proc = subprocess.run(step, cwd=p["cwd"], input=p.get("stdin"), capture_output=True, text=True, timeout=300, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            done.append({"argv": step, "rc": None, "error": str(e)})
            return {"ok": False, "steps": done}
        done.append({"argv": step, "rc": proc.returncode, "stderr": (proc.stderr or "")[-400:], "stdout": (proc.stdout or "")[-200:]})
        if proc.returncode != 0:
            return {"ok": False, "steps": done}
    return {"ok": True, "steps": done}


def apply_claim(store, claim: Claim, *, cwd: str, own: str | None, yes: bool, trust_eval: bool = False) -> dict[str, Any]:
    from .evaltrust import eval_trust

    trusted = eval_trust(store, claim) == "local"
    p = plan(claim, cwd)
    out: dict[str, Any] = {"id": claim.id, "plan": p, "trusted": trusted, "applied": False}
    if p.get("manual"):
        out["manual"] = p["manual"]
        return out
    if not yes:
        out["hint"] = "re-run with --yes to execute these steps and replay the eval"
        return out
    result = run_plan(p)
    out["run"] = result
    if not result["ok"]:
        out["error"] = "apply step failed; nothing was recorded"
        return out
    out["applied"] = True
    from .claim import _replay_now

    out["replay"] = _replay_now(claim.id, db=store.path, own=own, cwd=p["cwd"], trust_eval=trust_eval, mode="applied")
    if out["replay"].get("recorded"):
        # The hold is on record: the sensor's remembered failure for this tree or fingerprint is
        # consumed, as `claim --yes` does, so the next run or Stop hook does not ask the agent to
        # claim what was just recorded. A failure remembered elsewhere is left alone.
        from .env import forget_failure, last_failure

        rec = last_failure() or {}
        same_tree = os.path.abspath(str(rec.get("cwd") or "")) == p["cwd"] if rec.get("cwd") else False
        if rec and (same_tree or (rec.get("fp") and rec.get("fp") == claim.fp)):
            forget_failure()
    return out
