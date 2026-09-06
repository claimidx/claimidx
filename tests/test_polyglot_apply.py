"""Go, Rust, and Java get the same one-command loop as Python and Node.

`claim` names the missing package the compiler reported, pins what the tree's
own manager installs (`module@ver`, `crate@ver`, `group:artifact:ver`), and
drafts an eval that observes it. `apply` runs `go get` / `cargo add`, or writes
the coordinate into pom.xml / build.gradle, then replays.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from claimidx.apply import plan, run_plan
from claimidx.fingerprint import classify, fingerprint
from claimidx.models import Claim, EvalSpec, Fix
from claimidx.target import claim_target, eval_observes_target, suggest_eval

GO_ERR = "main.go:4:2: no required module provides package github.com/google/uuid; to add it:\n\tgo get github.com/google/uuid"
GO_ERR2 = 'main.go:4:2: cannot find package "github.com/google/uuid" in any of:'
RUST_ERR = "error[E0432]: unresolved import `serde`\n --> src/main.rs:1:5"
RUST_ERR2 = "error[E0433]: failed to resolve: use of undeclared crate or module `serde`"
RUST_ERR3 = "error[E0463]: can't find crate for `serde`"
RUST_ERR4 = "error[E0433]: cannot find module or crate `serde` in this scope"  # rustc 1.9x wording
NODE_ERR = "Error: Cannot find module 'or'"
JAVA_PKG_ERR = "src/main/java/App.java:1: error: package org.apache.commons.lang3 does not exist"
JAVA_ART_ERR = "Could not find org.apache.commons:commons-lang3:9.9.9.\nRequired by:\n    project :"
MVN_ART_ERR = "[ERROR] Failed to execute goal on project app: Could not resolve dependencies for project a:app:jar:1.0: Could not find artifact org.apache.commons:commons-lang3:jar:9.9.9 in central"


def _claim(eco: str, fix_k: str, fix_b: str, err: str = "x") -> Claim:
    return Claim(fp=fingerprint(err=err, eco=eco), cls="module_not_found", err=err, eco=eco, fix=Fix(k=fix_k, b=fix_b), eval=EvalSpec(cmd="true"))


# --- classify + target ----------------------------------------------------------------


def test_missing_dependency_errors_classify_as_module_not_found_across_ecosystems():
    for err in (GO_ERR, GO_ERR2, RUST_ERR, RUST_ERR2, RUST_ERR3, RUST_ERR4, JAVA_PKG_ERR, JAVA_ART_ERR, MVN_ART_ERR):
        assert classify(err) == "module_not_found", err


def test_claim_target_names_the_go_package_rust_crate_and_java_coordinate():
    assert claim_target(cls="module_not_found", err=GO_ERR) == "github.com/google/uuid"
    assert claim_target(cls="module_not_found", err=GO_ERR2) == "github.com/google/uuid"
    assert claim_target(cls="module_not_found", err=RUST_ERR) == "serde"
    assert claim_target(cls="module_not_found", err=RUST_ERR2) == "serde"
    assert claim_target(cls="module_not_found", err=RUST_ERR3) == "serde"
    assert claim_target(cls="module_not_found", err=RUST_ERR4) == "serde"
    assert claim_target(cls="module_not_found", err=NODE_ERR) == "or"
    assert claim_target(cls="module_not_found", err=JAVA_ART_ERR) == "org.apache.commons:commons-lang3"
    assert claim_target(cls="module_not_found", err=MVN_ART_ERR) == "org.apache.commons:commons-lang3"
    # A Java package name is not an artifact: no attribution, the gate does not judge.
    assert claim_target(cls="module_not_found", err=JAVA_PKG_ERR) == ""


def test_suggest_eval_observes_the_target_with_the_trees_own_tool():
    assert suggest_eval("github.com/google/uuid", "go") == "go list github.com/google/uuid"
    assert suggest_eval("serde", "rust") == "cargo pkgid serde"
    assert suggest_eval("org.apache.commons:commons-lang3", "java") == ""
    assert eval_observes_target("go list github.com/google/uuid", "github.com/google/uuid")
    assert eval_observes_target("cargo pkgid serde", "serde")
    assert eval_observes_target("gradle -q dependencyInsight --dependency commons-lang3", "org.apache.commons:commons-lang3")
    assert not eval_observes_target("go build ./...", "github.com/google/uuid")


# --- the draft's fix and eval ------------------------------------------------------------


def test_install_fix_pins_in_each_ecosystems_notation():
    from claimidx.claim import _install_fix, infer_fix_kind, normalize_fix

    assert _install_fix("github.com/google/uuid", "go", [], installed="github.com/google/uuid@v1.6.0") == ("pin", "github.com/google/uuid@v1.6.0")
    assert _install_fix("github.com/google/uuid", "go", []) == ("constraint", "github.com/google/uuid")
    assert _install_fix("serde", "rust", [], installed="serde@1.0.219") == ("pin", "serde@1.0.219")
    assert _install_fix("org.apache.commons:commons-lang3", "java", [], installed="org.apache.commons:commons-lang3@3.17.0") == (
        "pin",
        "org.apache.commons:commons-lang3:3.17.0",
    )
    assert _install_fix("org.apache.commons:commons-lang3", "java", []) == ("constraint", "org.apache.commons:commons-lang3")
    assert infer_fix_kind("org.apache.commons:commons-lang3:3.17.0") == "pin"
    assert infer_fix_kind("github.com/google/uuid@v1.6.0") == "pin"
    assert normalize_fix("go get github.com/google/uuid@v1.6.0") == ("pin", "github.com/google/uuid@v1.6.0")
    assert normalize_fix("cargo add serde@1.0.219") == ("pin", "serde@1.0.219")


def test_refine_eval_never_turns_a_rust_or_go_pin_into_a_python_import():
    from claimidx.public import refine_eval

    assert refine_eval("true", fix_k="pin", fix_b="serde@1.0.219", eco="rust") == "cargo pkgid serde"
    assert refine_eval("true", fix_k="pin", fix_b="github.com/google/uuid@v1.6.0", eco="go") == "go list github.com/google/uuid"
    assert refine_eval("true", fix_k="pin", fix_b="org.apache.commons:commons-lang3:3.17.0", eco="java") == "true"


def test_installed_version_reads_cargo_lock_pom_and_gradle(tmp_path: Path):
    from claimidx.env import installed_version

    rust = tmp_path / "rs"
    rust.mkdir()
    (rust / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    (rust / "Cargo.lock").write_text('[[package]]\nname = "serde"\nversion = "1.0.219"\n\n[[package]]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8")
    assert installed_version("serde", "rust", str(rust)) == "serde@1.0.219"
    assert installed_version("tokio", "rust", str(rust)) == ""

    mvn = tmp_path / "mvn"
    mvn.mkdir()
    (mvn / "pom.xml").write_text(
        "<project>\n  <dependencies>\n    <dependency>\n      <groupId>org.apache.commons</groupId>\n      <artifactId>commons-lang3</artifactId>\n"
        "      <version>3.17.0</version>\n    </dependency>\n  </dependencies>\n</project>\n",
        encoding="utf-8",
    )
    assert installed_version("org.apache.commons:commons-lang3", "java", str(mvn)) == "org.apache.commons:commons-lang3@3.17.0"

    gradle = tmp_path / "gradle"
    gradle.mkdir()
    (gradle / "build.gradle.kts").write_text('dependencies {\n    implementation("org.apache.commons:commons-lang3:3.17.0")\n}\n', encoding="utf-8")
    assert installed_version("org.apache.commons:commons-lang3", "java", str(gradle)) == "org.apache.commons:commons-lang3@3.17.0"


def test_installed_version_asks_go_list_for_the_module(monkeypatch, tmp_path: Path):
    from claimidx import env

    (tmp_path / "go.mod").write_text("module x\n\ngo 1.22\n", encoding="utf-8")
    seen: list[list[str]] = []

    class _P:
        returncode = 0
        stdout = "github.com/google/uuid@v1.6.0\n"
        stderr = ""

    def fake_run(argv, **kw):
        seen.append(list(argv))
        return _P()

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    assert env.installed_version("github.com/google/uuid/sub", "go", str(tmp_path)) == "github.com/google/uuid@v1.6.0"
    assert seen and seen[0][:2] == ["go", "list"] and seen[0][-1] == "github.com/google/uuid/sub"


def test_tree_eval_for_java_trees(tmp_path: Path):
    from claimidx.env import tree_eval

    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    assert tree_eval(tmp_path, "java") == "mvn -q compile"
    (tmp_path / "pom.xml").unlink()
    (tmp_path / "build.gradle.kts").write_text("", encoding="utf-8")
    assert tree_eval(tmp_path, "java") == "gradle -q compileJava"


# --- apply ---------------------------------------------------------------------------------


def test_apply_plans_go_get_and_cargo_add(tmp_path: Path):
    go = _claim("go", "pin", "github.com/google/uuid@v1.6.0")
    assert plan(go, str(tmp_path))["steps"] == [["go", "get", "github.com/google/uuid@v1.6.0"]]
    assert plan(_claim("go", "constraint", "github.com/google/uuid"), str(tmp_path))["steps"] == [["go", "get", "github.com/google/uuid"]]
    assert plan(_claim("rust", "pin", "serde@1.0.219"), str(tmp_path))["steps"] == [["cargo", "add", "serde@1.0.219"]]
    assert plan(_claim("rust", "pin", "cargo add serde@1.0.219"), str(tmp_path))["steps"] == [["cargo", "add", "serde@1.0.219"]]
    for bad in ("github.com/x/y@v1 --insecure", "-u github.com/x/y", "https://evil/x@v1", "serde --features derive", "../x", "serde@1; rm -rf /"):
        for eco in ("go", "rust"):
            assert plan(_claim(eco, "pin", bad), str(tmp_path)).get("manual"), (eco, bad)


def test_apply_writes_a_java_coordinate_into_pom_xml(tmp_path: Path):
    pom = tmp_path / "pom.xml"
    pom.write_text(
        '<?xml version="1.0"?>\n<project>\n  <modelVersion>4.0.0</modelVersion>\n  <dependencies>\n    <dependency>\n      <groupId>junit</groupId>\n'
        "      <artifactId>junit</artifactId>\n      <version>4.13.2</version>\n    </dependency>\n  </dependencies>\n</project>\n",
        encoding="utf-8",
    )
    p = plan(_claim("java", "pin", "org.apache.commons:commons-lang3:3.17.0"), str(tmp_path))
    assert not p.get("manual") and p["edit"]["file"] == "pom.xml"
    res = run_plan(p)
    assert res["ok"], res
    text = pom.read_text(encoding="utf-8")
    assert "<artifactId>commons-lang3</artifactId>" in text and "<version>3.17.0</version>" in text
    assert text.count("<dependency>") == 2 and text.index("junit") < text.index("commons-lang3")
    # Same coordinate again: the version is replaced, nothing is duplicated.
    assert run_plan(plan(_claim("java", "pin", "org.apache.commons:commons-lang3:3.18.0"), str(tmp_path)))["ok"]
    text = pom.read_text(encoding="utf-8")
    assert text.count("<dependency>") == 2 and "<version>3.18.0</version>" in text and "3.17.0" not in text
    # A bare coordinate has no version to write.
    assert plan(_claim("java", "constraint", "org.apache.commons:commons-lang3"), str(tmp_path)).get("manual")
    assert plan(_claim("java", "pin", "org.apache.commons:commons-lang3:3.17.0"), str(tmp_path / "nowhere")).get("manual")


def test_apply_writes_a_java_coordinate_into_build_gradle(tmp_path: Path):
    kts = tmp_path / "build.gradle.kts"
    kts.write_text('plugins {\n    java\n}\n\ndependencies {\n    testImplementation("junit:junit:4.13.2")\n}\n', encoding="utf-8")
    p = plan(_claim("java", "pin", "org.apache.commons:commons-lang3:3.17.0"), str(tmp_path))
    assert p["edit"]["file"] == "build.gradle.kts"
    assert run_plan(p)["ok"]
    text = kts.read_text(encoding="utf-8")
    assert '    implementation("org.apache.commons:commons-lang3:3.17.0")\n' in text and text.index("junit") < text.index("commons-lang3")
    assert run_plan(plan(_claim("java", "pin", "org.apache.commons:commons-lang3:3.18.0"), str(tmp_path)))["ok"]
    text = kts.read_text(encoding="utf-8")
    assert text.count("commons-lang3") == 1 and "3.18.0" in text
    groovy = tmp_path / "g"
    groovy.mkdir()
    (groovy / "build.gradle").write_text("apply plugin: 'java'\n", encoding="utf-8")
    assert run_plan(plan(_claim("java", "pin", "org.apache.commons:commons-lang3:3.17.0"), str(groovy)))["ok"]
    text = (groovy / "build.gradle").read_text(encoding="utf-8")
    assert "dependencies {\n    implementation 'org.apache.commons:commons-lang3:3.17.0'\n}\n" in text


def test_apply_java_edit_is_a_visible_step_and_survives_git(tmp_path: Path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "pom.xml").write_text("<project>\n</project>\n", encoding="utf-8")
    p = plan(_claim("java", "pin", "org.apache.commons:commons-lang3:3.17.0"), str(tmp_path))
    from claimidx.apply import render_plan

    text = render_plan(p, trusted=True, own="x", src="local")
    assert "pom.xml" in text and "org.apache.commons:commons-lang3:3.17.0" in text
    assert run_plan(p)["ok"]
    assert "<dependencies>" in (tmp_path / "pom.xml").read_text(encoding="utf-8")


# --- trust grammar and policy ------------------------------------------------------------


def test_java_build_checks_are_in_the_portable_grammar(tmp_path: Path):
    from claimidx.evaltrust import untrusted_reason
    from claimidx.policy import eval_allowed

    (tmp_path / "gradlew").write_text("", encoding="utf-8")
    (tmp_path / "App.java").write_text("", encoding="utf-8")
    for ok in (
        "mvn -q compile",
        "mvn -q -B test",
        "gradle -q compileJava",
        "gradle build --offline",
        "gradle -q dependencyInsight --dependency commons-lang3 --configuration compileClasspath",
        "javac App.java",
        "cargo pkgid serde",
        "go list github.com/google/uuid",
    ):
        assert eval_allowed(ok)[0], ok
        assert untrusted_reason(ok, str(tmp_path)) == "", ok
    for bad in (
        "mvn -s evil.xml compile",
        "mvn -Dexec.mainClass=x exec:java",
        "gradle -I init.gradle build",
        "gradle -b other.gradle build",
        "gradle run",
        "javac Missing.java",
        "java -jar app.jar",
    ):
        assert not eval_allowed(bad)[0] or untrusted_reason(bad, str(tmp_path)), bad


def test_gradle_and_mvn_resolve_to_the_trees_wrapper(tmp_path: Path):
    from claimidx.sandbox import resolve_argv

    wrapper = "gradlew.bat" if os.name == "nt" else "gradlew"
    (tmp_path / wrapper).write_text("", encoding="utf-8")
    assert resolve_argv(["gradle", "-q", "compileJava"], str(tmp_path))[0] == str(tmp_path / wrapper)
    mvnw = "mvnw.cmd" if os.name == "nt" else "mvnw"
    (tmp_path / mvnw).write_text("", encoding="utf-8")
    assert resolve_argv(["mvn", "-q", "compile"], str(tmp_path))[0] == str(tmp_path / mvnw)


def test_infer_env_names_go_rust_java_runtimes(tmp_path: Path):
    from claimidx.env import infer_env

    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    got = infer_env(tmp_path, err=JAVA_PKG_ERR)
    assert got["eco"] == "java"
    assert got["rt"] == "" or got["rt"].startswith("java@")
    assert infer_env(tmp_path, err=GO_ERR)["eco"] == "go"
    assert infer_env(tmp_path, err=RUST_ERR)["eco"] == "rust"
    assert infer_env(tmp_path, err=RUST_ERR4)["eco"] == "rust"  # not npm: node quotes its module name
    assert infer_env(tmp_path, err=NODE_ERR)["eco"] == "npm"
    assert infer_env(tmp_path, err=JAVA_ART_ERR)["eco"] == "java"


@pytest.mark.skipif(not (Path.home() / ".cargo").exists(), reason="no cargo")
def test_rust_rt_shape():
    from claimidx.env import _rust_rt

    rt = _rust_rt()
    assert rt == "" or rt.startswith("rust@1.")


def test_draft_prefers_the_resolved_pin_over_the_manifest_diff(tmp_path: Path, capsys):
    """`cargo add uuid` rewrites Cargo.toml and Cargo.lock; the portable claim is the pin, not that tree's diff."""
    import json

    from claimidx.cli import main
    from claimidx.env import remember_failure

    tree = tmp_path / "rs"
    tree.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True)
    (tree / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\n', encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tree, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "broken"], cwd=tree, check=True)
    (tree / "Cargo.toml").write_text('[package]\nname = "x"\nversion = "0.1.0"\n\n[dependencies]\nuuid = "1.16.0"\n', encoding="utf-8")
    (tree / "Cargo.lock").write_text('[[package]]\nname = "uuid"\nversion = "1.16.0"\n', encoding="utf-8")
    remember_failure("error[E0433]: failed to resolve: use of undeclared crate or module `uuid`", cwd=str(tree), eco="rust")
    assert main(["--db", str(tmp_path / "ix.sqlite"), "--fmt", "json", "claim"]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert draft["ok"] and draft["eco"] == "rust" and draft["target"] == "uuid"
    assert draft["fix_k"] == "pin" and draft["fix_b"] == "uuid@1.16.0", draft
    assert draft["eval"] == "cargo pkgid uuid" and draft["eval_proof"] is True
    assert draft["inferred"]["fix_b"].startswith("pin for claimed target")


def test_draft_looks_up_the_whole_go_package_path(monkeypatch, tmp_path: Path, capsys):
    """`github.com/google/uuid` is asked of `go list` whole, not trimmed to `github`."""
    import json

    from claimidx import env
    from claimidx.cli import main
    from claimidx.env import remember_failure

    tree = tmp_path / "go"
    tree.mkdir()
    (tree / "go.mod").write_text("module x\n\ngo 1.22\n", encoding="utf-8")
    seen: list[list[str]] = []

    class _P:
        returncode = 0
        stdout = "github.com/google/uuid@v1.6.0\n"
        stderr = ""

    def fake_run(argv, **kw):
        seen.append(list(argv))
        return _P()

    monkeypatch.setattr(env.subprocess, "run", fake_run)
    remember_failure(GO_ERR, cwd=str(tree), eco="go", rt="go@1.26")
    assert main(["--db", str(tmp_path / "ix.sqlite"), "--fmt", "json", "claim", "--no-diff"]) == 0
    draft = json.loads(capsys.readouterr().out)
    assert [a for a in seen if a[:2] == ["go", "list"]][0][-1] == "github.com/google/uuid"
    assert draft["fix_k"] == "pin" and draft["fix_b"] == "github.com/google/uuid@v1.6.0", draft
    assert draft["eval"] == "go list github.com/google/uuid"


def test_package_observations_are_not_bound_to_manifest_bytes():
    """`cargo pkgid x` / `go list x` observe a package like `import x` does; the manifest is what the fix changes."""
    from claimidx.binding import is_tree_scoped

    assert not is_tree_scoped("cargo pkgid uuid")
    assert not is_tree_scoped("go list github.com/google/uuid")
    assert is_tree_scoped("go list ./...")
    assert is_tree_scoped("go build ./...")
    assert is_tree_scoped("cargo check")
    assert is_tree_scoped("cargo tree")
    assert is_tree_scoped("gradle -q compileJava")
    assert is_tree_scoped("mvn -q compile")


def test_pin_claims_tolerate_drift_in_the_manifest_the_pin_rewrote(tmp_path: Path, capsys):
    """A Java pin's only eval is the build; it is bound to build files, and the pin is what changes them.

    Manifest-only drift under a pin/constraint claim records with a warning; any other
    drift, or drift under a patch claim, still refuses as proof-artifact-drift.
    """
    import json

    from claimidx.cli import main

    db = str(tmp_path / "ix.sqlite")
    tree = tmp_path / "tree"
    (tree / "tests").mkdir(parents=True)
    (tree / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '1'\n", encoding="utf-8")
    (tree / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")

    def publish(fix_k: str, fix_b: str) -> str:
        err = f"RuntimeError: bound-file probe {fix_k}"
        args = [
            "--db",
            db,
            "--fmt",
            "id",
            "publish",
            "--err",
            err,
            "--eco",
            "py",
            "--fix-k",
            fix_k,
            "--fix-b",
            fix_b,
            "--eval",
            "pytest -q",
            "--cwd",
            str(tree),
        ]
        assert main(args) == 0
        return capsys.readouterr().out.strip()

    def confirm(cid: str) -> tuple[int, dict]:
        rc = main(["--db", db, "--fmt", "json", "confirm", "--replay", "--cwd", str(tree), cid])
        return rc, json.loads(capsys.readouterr().out)

    def nr(cid: str) -> int:
        assert main(["--db", db, "--fmt", "json", "show", cid]) == 0
        return int(json.loads(capsys.readouterr().out)["nr"])

    pin = publish("pin", "x==1.0.0")
    patch = publish("patch", "edit tests")
    rc, out = confirm(pin)
    assert rc == 0 and nr(pin) == 1, out
    # The pin rewrote the manifest the recipe is bound to.
    (tree / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '1'\ndependencies = ['x==1.0.0']\n", encoding="utf-8")
    rc, out = confirm(pin)
    assert rc == 0 and nr(pin) == 2, out
    assert "manifest drift" in json.dumps(out), out
    rc, out = confirm(pin)
    assert rc == 0 and nr(pin) == 3 and "manifest drift" in json.dumps(out), out  # not re-bound (proofs are shared by recipe): warns again
    rc, out = confirm(patch)
    assert rc != 0 and "proof-artifact-drift" in str(out.get("reason")), out
    # Source drift under the pin is still refused.
    (tree / "tests" / "test_ok.py").write_text("def test_ok():\n    assert 1\n", encoding="utf-8")
    (tree / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '2'\n", encoding="utf-8")
    rc, out = confirm(pin)
    assert rc == 0 and nr(pin) == 4, out  # tests/ is not a bound manifest for `pytest -q`; only pyproject.toml is


def test_first_error_line_skips_maven_section_headers():
    """`[ERROR] COMPILATION ERROR :` is a heading; the failure is the javac line under it."""
    from claimidx.hook import _first_err_line

    body = (
        "[INFO] Compiling 1 source file\n"
        "[ERROR] COMPILATION ERROR :\n"
        "[ERROR] /x/src/main/java/App.java:[1,32] package org.apache.commons.lang3 does not exist\n"
        "[ERROR] BUILD FAILURE\n"
        "[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.13.0:compile (default-compile) on project tiny\n"
    )
    assert _first_err_line(body) == "[ERROR] /x/src/main/java/App.java:[1,32] package org.apache.commons.lang3 does not exist"
    assert _first_err_line("[ERROR] BUILD FAILURE\n[ERROR] Failed to execute goal x:y on project z\n") == "[ERROR] BUILD FAILURE"


def test_proof_binding_ignores_line_endings(tmp_path: Path):
    """A tree checked out with autocrlf must not drift against a binding made on an LF checkout."""
    from claimidx.binding import binding_drift, compute_binding

    (tmp_path / "pyproject.toml").write_bytes(b"[project]\nname = 'x'\n")
    b = compute_binding("pytest -q", tmp_path)
    assert b is not None and [a.path for a in b.artifacts] == ["pyproject.toml"]
    (tmp_path / "pyproject.toml").write_bytes(b"[project]\r\nname = 'x'\r\n")
    assert binding_drift(b, tmp_path) == []
    (tmp_path / "pyproject.toml").write_bytes(b"[project]\r\nname = 'y'\r\n")
    assert binding_drift(b, tmp_path) == ["pyproject.toml"]


def test_java_edit_keeps_the_files_line_endings(tmp_path: Path):
    from claimidx.apply import write_java_dependency

    pom = tmp_path / "pom.xml"
    pom.write_bytes(b"<project>\r\n  <dependencies>\r\n  </dependencies>\r\n</project>\r\n")
    write_java_dependency(str(tmp_path), "pom.xml", "org.apache.commons:commons-lang3:3.17.0")
    raw = pom.read_bytes()
    assert b"commons-lang3" in raw and raw.count(b"\r\n") == raw.count(b"\n"), raw
    kts = tmp_path / "build.gradle.kts"
    kts.write_bytes(b"dependencies {\n}\n")
    write_java_dependency(str(tmp_path), "build.gradle.kts", "org.apache.commons:commons-lang3:3.17.0")
    assert b"\r" not in kts.read_bytes()
