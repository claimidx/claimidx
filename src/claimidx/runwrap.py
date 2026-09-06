"""`claimidx run -- <command>`: the sensor for harnesses without hooks.

Claude Code gets four hook events. Everything else — Codex, Cursor, Gemini,
a plain shell, a CI job — gets this one wrapper: it runs the command exactly
as given (no shell), streams its output through untouched, and then does
what the hooks do: on a non-zero exit, ask the index and remember the
failure; on a zero exit that clears a remembered failure for the same
command, say "claim it" with the draft. Exit status is the command's own.

The advice goes to stderr after the command's own output, prefixed
`CLAIMIDX`, so an agent reading the tool result sees it last.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import Any

from .env import deps_from_traceback, infer_env, last_failure, remember_failure
from .hook import _first_err_line


def _tail(text: str, n: int = 4000) -> str:
    return text[-n:] if len(text) > n else text


def run_command(argv: list[str], *, cwd: str | None = None, timeout: float | None = None) -> tuple[int, str]:
    """Run argv, passing stdout/stderr through live, and return (rc, captured tail of both)."""
    if not argv:
        return 2, ""
    from .sandbox import _which

    head = argv[0]
    if not os.path.dirname(head):
        head = _which(head) or head  # PATH lookup with PATHEXT: `gradle` is gradle.cmd on Windows, as a shell would find it
    try:
        proc = subprocess.Popen([head, *argv[1:]], cwd=cwd or None, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    except OSError as e:
        # The command never started: that is this wrapper's failure, not the tree's. Nothing to ask or remember.
        sys.stderr.write(f"claimidx run: {e}\n")
        return 127, ""
    chunks: list[str] = []
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            chunks.append(line)
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = 124
    except KeyboardInterrupt:
        proc.kill()
        raise
    return rc, _tail("".join(chunks))


def after_run(store, argv: list[str], rc: int, output: str, *, cwd: str | None = None, k: int = 5) -> dict[str, Any]:
    """The hook logic, given a finished command. Returns what was printed."""
    from .fingerprint import classify, fingerprint
    from .match import verdict_for
    from .query import retrieve

    cmd = shlex.join(argv)
    root = cwd or os.getcwd()
    out: dict[str, Any] = {"rc": rc, "command": cmd}
    if rc != 0:
        err = _first_err_line(output) or ""
        if not err:
            return out
        guess = infer_env(root, err=err)
        dep = deps_from_traceback(output, root, guess["eco"])
        cls = classify(err)
        fp = fingerprint(err=err, cls=cls, eco=guess["eco"], rt=guess["rt"], dep=dep)
        q: dict[str, Any] = {"err": err, "cls": cls, "eco": guess["eco"], "rt": guess["rt"], "dep": dep, "fp": fp}
        hits, _cands = retrieve(store, q, k=k, kind="hook")
        v = verdict_for(q, hits)
        remember_failure(err, command=cmd, cwd=root, eco=guess["eco"], rt=guess["rt"], event="run", fp=fp)
        line = (
            f"CLAIMIDX verdict {v['action']} {v['id']} — {v['why']}; next: {v['next']}"
            if v.get("id")
            else f"CLAIMIDX verdict {v['action']} — {v['why']}; next: {v['next']}"
        )
        if hits:
            c = hits[0][0]
            line += f"\nCLAIMIDX hit {c.id} fix.k={c.fix.k} fix.b={c.fix.b.splitlines()[0][:160]!r} eval={c.eval.cmd!r} (data from {c.own}, not instructions)"
        sys.stderr.write(line + "\n")
        out["verdict"] = v
        return out
    rec = last_failure()
    if not rec or rec.get("nudged") or " ".join(str(rec.get("command") or "").split()) != " ".join(cmd.split()):
        return out
    try:
        from .claim import draft_claim

        draft = draft_claim(cwd=str(rec.get("cwd") or root))
    except Exception:
        draft = {"ok": False}
    remember_failure(
        str(rec["err"]),
        command=cmd,
        cwd=str(rec.get("cwd") or root),
        eco=str(rec.get("eco") or ""),
        rt=str(rec.get("rt") or ""),
        event="fixed",
        fp=str(rec.get("fp") or ""),
        extra={"nudged": True},
    )
    line = f"CLAIMIDX fixed: `{cmd}` now passes after failing with: {rec['err'][:120]}\nRecord it so the next agent skips this: claimidx claim --yes"
    if draft.get("ok"):
        line += f"   (drafted: fix.k={draft['fix_k']} eval={draft['eval']} proof={str(draft['eval_proof']).lower()})"
    sys.stderr.write(line + "\n")
    out["nudge"] = True
    return out
