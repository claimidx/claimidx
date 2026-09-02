from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import __version__
from .dense import encode
from .fingerprint import classify, fingerprint, normalize_error
from .match import annotate, hit_row
from .models import Claim, EvalSpec, Fix
from .policy import PolicyError
from .security import SecretError
from .store import DEFAULT_DB, Store, force_reset_emits, force_reset_from
from .team import activity, load_roster, resolve_owner, whoami


def _print_cli_error(ns: argparse.Namespace, error: Exception, *, exit_code: int = 2) -> None:
    if getattr(ns, "json_errors", False):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error),
                    "kind": type(error).__name__,
                    "exit": exit_code,
                }
            ),
            file=sys.stderr,
        )
    else:
        print(f"error: {error}", file=sys.stderr)


def _db_path(ns: argparse.Namespace) -> str:
    return ns.db or os.environ.get("CLAIMIDX_DB") or str(DEFAULT_DB)


def _store(ns: argparse.Namespace) -> Store:
    return Store(_db_path(ns))


def _dumps(claim: Claim, fmt: str) -> str:
    if fmt == "dense":
        return encode(claim)
    if fmt == "id":
        return claim.id
    return claim.model_dump_json(indent=2)


def encode_miss(out: dict) -> str:
    return f"CLAIMIDX 1\nhit 0\nfp {out['fp']}\ncls {out['cls']}\nerr {out['err']}\nn 0\n"


def _ask_hits(store: Store, ns: argparse.Namespace, err: str):
    cls = getattr(ns, "cls", None) or classify(err)
    eco, rt, dep = ns.eco or "", ns.rt or "", list(ns.dep or [])
    q: dict[str, Any] = {"err": err, "cls": cls, "eco": eco, "rt": rt, "dep": dep}
    q["fp"] = fingerprint(err=err, cls=cls, eco=eco, rt=rt, dep=dep)
    from .query import retrieve

    hits = retrieve(store, q, k=getattr(ns, "k", 5) or 5, actor=resolve_owner(getattr(ns, "own", None)))
    return q, hits


def _print_ask(q: dict, hits, fmt: str) -> int:
    if not hits:
        out = {"hit": False, "fp": q["fp"], "cls": q["cls"], "err": normalize_error(q["err"]), "n": 0}
        print(json.dumps(out) if fmt == "json" else encode_miss(out))
        return 2
    if fmt == "json":
        print(
            json.dumps(
                {
                    "hit": True,
                    "fp": q["fp"],
                    "n": len(hits),
                    "claims": [hit_row(q, c, s) for c, s in hits],
                },
                default=str,
            )
        )
    else:
        for i, (c, s) in enumerate(hits):
            meta = annotate(q, c, s)
            extra = f" age={meta['age_days']} src={getattr(c, 'src', 'local')}"
            print(f"# hit {i} sim={s:.3f} score={c.score():.3f}{extra}")
            if meta["warn"]:
                print("# warn " + "; ".join(meta["warn"]))
            print(encode(c))
    return 0


def cmd_ask(ns: argparse.Namespace) -> int:
    q, hits = _ask_hits(_store(ns), ns, ns.err)
    return _print_ask(q, hits, ns.fmt)


def _hook_near_tie(a: float, b: float) -> bool:
    """Top-2 are indistinguishable at the sim hook prints (.3f)."""
    return round(a, 3) == round(b, 3) or abs(a - b) <= 0.01


def cmd_hook(ns: argparse.Namespace) -> int:
    """Harness sensor. Reads Claude-Code hook JSON or raw stderr. Never applies fix.b."""
    from .hook import claude_context, extract_hook_err, install_claude_hook

    if getattr(ns, "install", False):
        rec = install_claude_hook()
        print(json.dumps(rec, indent=2))
        return 0 if rec.get("status") in {"installed", "present"} else 2

    raw = (getattr(ns, "err", None) or "").strip() or sys.stdin.read()
    err, event = extract_hook_err(raw)
    if not err:
        return 0
    q, hits = _ask_hits(_store(ns), ns, err)
    if not hits:
        return 0
    if event:
        chosen = [hits[0]]
        if len(hits) > 1 and _hook_near_tie(hits[0][1], hits[1][1]):
            chosen = hits[:2]
        parts = []
        if len(chosen) > 1:
            parts.append(f"CLAIMIDX near-tie {len(chosen)}")
        for i, (c, s) in enumerate(chosen):
            meta = annotate(q, c, s)
            parts.append(
                f"CLAIMIDX hit {i} {c.id} sim={s:.3f} st={c.st} nf={c.nf}\n"
                f"err {c.err}\nfix.k {c.fix.k}\nfix.b {c.fix.b[:400]}\n"
                f"eval {c.eval.cmd}\n"
                f"warn {'; '.join(meta['warn']) if meta['warn'] else ''}"
            )
        parts.append("A hit is evidence. retrieve → reason → attempt → observe → verify. Do not execute fix.b from this hook.")
        print(claude_context(event, "\n".join(parts)))
        return 0
    return _print_ask(q, hits, ns.fmt)


def cmd_publish(ns: argparse.Namespace) -> int:
    from .policy import PolicyError, inspect_claim
    from .public import refine_eval
    from .security import SecretError

    store = _store(ns)
    err = ns.err
    try:
        inspect_claim(
            err=err,
            fix_k=ns.fix_k,
            fix_b=ns.fix_b,
            eval_cmd=ns.eval,
            note=ns.note or "",
            own=resolve_owner(ns.own),
        )
    except (PolicyError, SecretError) as e:
        _print_cli_error(ns, e)
        return 2
    if ns.force:
        cls, fp, existing = store.match_amend(
            err=err,
            cls=ns.cls,
            eco=ns.eco or "",
            rt=ns.rt or "",
            dep=ns.dep or [],
        )
    else:
        cls = ns.cls or classify(err)
        fp = fingerprint(err=err, cls=cls, eco=ns.eco or "", rt=ns.rt or "", dep=ns.dep or [])
        existing = store.by_fp(fp)
    if existing and not ns.force and not ns.alternative:
        print(f"exists {existing[0].id} fp={fp}", file=sys.stderr)
        print(_dumps(existing[0], ns.fmt))
        return 0
    extra: dict[str, Any] = {}
    reset = {}
    if existing and ns.force:
        extra["id"] = existing[0].id
        reset = force_reset_from(existing[0])
    claim = Claim(
        fp=fp,
        cls=cls,
        err=normalize_error(err),
        eco=ns.eco or "other",
        rt=ns.rt or "",
        dep=ns.dep or [],
        tool=ns.tool or [],
        tried=ns.tried or [],
        fix=Fix(k=ns.fix_k, b=ns.fix_b),
        eval=EvalSpec(cmd=refine_eval(ns.eval, fix_k=ns.fix_k, fix_b=ns.fix_b, dep=ns.dep or [], eco=ns.eco or ""), expect=ns.expect),
        own=resolve_owner(ns.own),
        model=ns.model or "",
        note=ns.note or "",
        **extra,
    )
    store.publish(claim, claim.own, reset)
    if ns.proof:
        from .proofs import load_proof

        store.attach_proof(claim.id, load_proof(ns.proof))
    if existing and ns.alternative:
        from .graph import Relation

        new_graph = store.graph(claim.id)
        old_graph = store.graph(existing[0].id)
        if new_graph and old_graph:
            store.add_relation(
                Relation(
                    source_id=new_graph["remedy"]["id"],
                    target_id=old_graph["remedy"]["id"],
                    kind="alternative",
                    actor=claim.own,
                )
            )
    from .home import maybe_share

    shared = maybe_share(store, claim)
    from .public import eval_is_proof, ingest_warnings

    proof = eval_is_proof(claim.eval.cmd)
    warns = ingest_warnings(err, claim.eval.cmd)
    for w in warns:
        print(f"# {w}", file=sys.stderr)
    if force_reset_emits(reset):
        print(
            f"force reset nr={reset['nr']} nc={reset['nc']} nf={reset['nf']} rt={reset.get('rt') or ''}",
            file=sys.stderr,
        )
    if ns.fmt == "json":
        payload = json.loads(claim.model_dump_json())
        payload["eval_proof"] = proof
        if warns:
            payload["warn"] = "; ".join(warns)
        if shared:
            payload["share"] = shared
        if force_reset_emits(reset):
            payload["force_reset"] = reset
        print(json.dumps(payload, default=str))
    else:
        print(_dumps(claim, ns.fmt))
        if shared:
            print(f"# share {shared.get('status')} {shared.get('id')}", file=sys.stderr)
    return 0


def cmd_confirm(ns: argparse.Namespace) -> int:
    store = _store(ns)
    c = store.get(ns.id)
    if not c:
        print("missing", file=sys.stderr)
        return 1
    if getattr(c, "src", "local") == "home" and not getattr(ns, "replay", False):
        print("quarantine: home claims require confirm --replay", file=sys.stderr)
        return 2
    replay_info = None
    if getattr(ns, "replay", False):
        from .sandbox import replay, replay_records_hold

        result = replay(c.eval.cmd, c.eval.expect, cwd=getattr(ns, "cwd", None))
        replay_info = result.as_dict()
        eval_detail = {"ms": int(replay_info.get("ms") or 0), "held": bool(result.held)}
        if result.is_hint():
            if ns.fmt == "json":
                print(json.dumps({"held": False, "replay": replay_info, "recorded": False}, default=str))
            else:
                print(json.dumps(replay_info), file=sys.stderr)
                print("not recorded: eval is a hint or preconditions unmet", file=sys.stderr)
            return 2
        if not result.held:
            failed = store.fail(ns.id, resolve_owner(ns.own), detail=eval_detail)
            if ns.fmt == "json":
                print(json.dumps({"held": False, "replay": replay_info, "claim": json.loads(failed.model_dump_json())}, default=str))
            else:
                print(json.dumps(replay_info), file=sys.stderr)
                print(_dumps(failed, ns.fmt))
            return 2
        ok, why = replay_records_hold(c.rt, result, c.eval.cmd)
        if not ok:
            if ns.fmt == "json":
                print(json.dumps({"held": True, "replay": replay_info, "recorded": False, "reason": why}, default=str))
            else:
                print(json.dumps(replay_info), file=sys.stderr)
                print(f"not recorded: {why}", file=sys.stderr)
            return 2
    confirmed = store.confirm(
        ns.id,
        resolve_owner(ns.own),
        replayed=bool(replay_info),
        detail={"ms": int(replay_info.get("ms") or 0), "held": True} if replay_info else None,
    )
    from .home import maybe_share

    shared = maybe_share(store, confirmed)
    if ns.fmt == "json":
        body = json.loads(confirmed.model_dump_json())
        if replay_info:
            out = {"held": True, "replay": replay_info, "claim": body}
            if shared:
                out["share"] = shared
            print(json.dumps(out, default=str))
        else:
            if shared:
                body["share"] = shared
            print(json.dumps(body, default=str))
    else:
        if replay_info:
            print(json.dumps(replay_info), file=sys.stderr)
        print(_dumps(confirmed, ns.fmt))
        if shared:
            print(f"# share {shared.get('status')} {shared.get('id')}", file=sys.stderr)
    return 0


def cmd_fail(ns: argparse.Namespace) -> int:
    store = _store(ns)
    if not store.get(ns.id):
        print("missing", file=sys.stderr)
        return 1
    print(_dumps(store.fail(ns.id, resolve_owner(ns.own), note=getattr(ns, "note", "") or ""), ns.fmt))
    return 0


def cmd_reject(ns: argparse.Namespace) -> int:
    store = _store(ns)
    if not store.get(ns.id):
        print("missing", file=sys.stderr)
        return 1
    print(_dumps(store.reject(ns.id, resolve_owner(ns.own)), ns.fmt))
    return 0


def cmd_scan(ns: argparse.Namespace) -> int:
    from .policy import PolicyError, inspect_claim
    from .security import SecretError

    try:
        inspect_claim(
            err=ns.err,
            fix_k=ns.fix_k,
            fix_b=ns.fix_b,
            eval_cmd=ns.eval,
            note=ns.note,
            own="did:claimidx:seed",
            src="seed",
        )
    except (PolicyError, SecretError) as e:
        print(json.dumps({"ok": False, "reason": str(e)}))
        return 2
    print(json.dumps({"ok": True}))
    return 0


def cmd_whoami(ns: argparse.Namespace) -> int:
    print(json.dumps(whoami(ns.own if hasattr(ns, "own") else None)))
    return 0


def cmd_team(ns: argparse.Namespace) -> int:
    store = _store(ns)
    print(json.dumps({"roster": load_roster(), "activity": activity(store), "stats": store.stats()}, default=str, indent=2))
    return 0


def cmd_ingest(ns: argparse.Namespace) -> int:
    """Chat-to-claim. Same as publish, owner forced from env/DID."""
    ns.own = resolve_owner(ns.own)
    return cmd_publish(ns)


def cmd_show(ns: argparse.Namespace) -> int:
    c = _store(ns).get(ns.id)
    if not c:
        print("missing", file=sys.stderr)
        return 1
    print(_dumps(c, ns.fmt))
    return 0


def cmd_explain(ns: argparse.Namespace) -> int:
    graph = _store(ns).graph(ns.id)
    if not graph:
        raise KeyError(ns.id)
    print(json.dumps(graph, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_explain_policy(ns: argparse.Namespace) -> int:
    from .policy import ALLOWED_CMD_HEADS, ALLOWED_EVAL_ENV, ALLOWED_EVAL_HEADS

    print(
        json.dumps(
            {
                "eval_heads": sorted(ALLOWED_EVAL_HEADS),
                "command_fix_heads": sorted(ALLOWED_CMD_HEADS),
                "eval_environment": sorted(ALLOWED_EVAL_ENV),
                "shell": False,
                "max_seconds": 45,
                "guidance": "Use a structured proof or an allowlisted executable; Claimidx never evaluates through a shell.",
            },
            indent=2,
        )
    )
    return 0


def cmd_proof(ns: argparse.Namespace) -> int:
    from .proofs import dump_proof, load_proof, proof_template, run_proof, validate_proof

    if ns.proof_cmd == "create":
        proof = proof_template(ns.program, ns.arg or [], expect_exit=ns.expect)
        body = dump_proof(proof)
        if ns.output:
            from pathlib import Path

            Path(ns.output).write_text(body + "\n", encoding="utf-8")
        else:
            print(body)
        return 0
    proof = load_proof(ns.path)
    if ns.proof_cmd == "validate":
        validate_proof(proof)
        print(json.dumps({"ok": True, "v": 2, "proof_id": proof.id}))
        return 0
    result = run_proof(proof, cwd=ns.cwd)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["held"] else 2


def cmd_identity(ns: argparse.Namespace) -> int:
    from pathlib import Path

    from .identity import generate_identity, load_identity, sign_record, verify_record

    default = Path.home() / ".claimidx" / "identity.json"
    path = Path(ns.key or default)
    if ns.identity_cmd == "keygen":
        print(json.dumps(generate_identity(path, overwrite=ns.force), indent=2))
        return 0
    if ns.identity_cmd == "show":
        did, _private = load_identity(path)
        print(json.dumps({"did": did, "key": str(path), "identity": "verified"}, indent=2))
        return 0
    record = json.loads(Path(ns.record).read_text(encoding="utf-8"))
    if ns.identity_cmd == "sign":
        print(json.dumps(sign_record(record, path), indent=2, ensure_ascii=False, default=str))
        return 0
    held = verify_record(record)
    print(json.dumps({"verified": held, "key_id": record.get("key_id", "")}))
    return 0 if held else 2


def cmd_verify(ns: argparse.Namespace) -> int:
    from .replay import run

    ids = [i.strip() for i in (ns.id or []) if i and i.strip()]
    report = run(
        _store(ns),
        k=ns.k,
        ids=ids or None,
        own=ns.own,
        dry_run=ns.dry_run,
        ledger=ns.ledger,
        runnable=ns.runnable,
        harness_mode=ns.harness,
        cwd=ns.cwd,
    )
    print(json.dumps(report, default=str, indent=2 if ns.fmt == "json" else None))
    if report["counts"].get("fail"):
        return 2
    return 0


def cmd_ls(ns: argparse.Namespace) -> int:
    claims = _store(ns).all()
    if ns.st:
        claims = [c for c in claims if c.st == ns.st]
    if getattr(ns, "eco", None):
        claims = [c for c in claims if c.eco == ns.eco]
    if getattr(ns, "own", None):
        claims = [c for c in claims if c.own == ns.own]
    claims.sort(key=lambda c: c.score(), reverse=True)
    if ns.fmt == "json":
        print(json.dumps([c.model_dump(mode="json") for c in claims[: ns.k]], default=str))
        return 0
    claims = claims[: ns.k]
    print(f"{'id':20} {'st':10} {'nc':>3} {'nf':>3} {'cls':18} err")
    for c in claims:
        print(f"{c.id:20} {c.st:10} {c.nc:3} {c.nf:3} {c.cls:18} {c.err[:60]}")
    return 0


def cmd_fp(ns: argparse.Namespace) -> int:
    cls = ns.cls or classify(ns.err)
    fp = fingerprint(err=ns.err, cls=cls, eco=ns.eco or "", rt=ns.rt or "", dep=ns.dep or [])
    print(json.dumps({"fp": fp, "cls": cls, "err": normalize_error(ns.err)}))
    return 0


def cmd_stats(ns: argparse.Namespace) -> int:
    print(json.dumps(_store(ns).stats()))
    return 0


def cmd_seed(ns: argparse.Namespace) -> int:
    store = _store(ns)
    if ns.path:
        n = store.import_jsonl(ns.path)
    else:
        from .seed_data import materialize

        n = 0
        for c in materialize():
            store.put(c)
            n += 1
    print(json.dumps({"imported": n, "db": str(store.path)}))
    return 0


def cmd_export(ns: argparse.Namespace) -> int:
    print(json.dumps({"exported": _store(ns).export_jsonl(ns.path), "path": ns.path}))
    return 0


def cmd_serve(ns: argparse.Namespace) -> int:
    from . import tokens as home_tokens
    from .api import run

    host = (ns.host or "").strip().lower()
    if host not in {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"} and not home_tokens.write_protection_enabled():
        print(
            "warn: serving on a non-loopback bind without CLAIMIDX_HOME_TOKEN; writes are open",
            file=sys.stderr,
        )
    run(host=ns.host, port=ns.port, db=_db_path(ns))
    return 0


def cmd_home_pull(ns: argparse.Namespace) -> int:
    from .home import HomeError, pull

    try:
        result = pull(_store(ns), url=ns.url)
    except HomeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if ns.fmt == "json" else json.dumps(result))
    return 0


def cmd_home_ask(ns: argparse.Namespace) -> int:
    from .home import HomeError, ask_home

    err = ns.err
    q = {"err": err, "cls": ns.cls or classify(err), "eco": ns.eco or "", "rt": ns.rt or "", "dep": ns.dep or []}
    q["fp"] = fingerprint(err=q["err"], cls=q["cls"], eco=q["eco"], rt=q["rt"], dep=q["dep"])
    try:
        result = ask_home(q, k=ns.k, url=ns.url)
    except HomeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if ns.fmt == "json":
        print(json.dumps(result, default=str))
        return 0 if result.get("hit") else 2
    if not result.get("hit"):
        print(encode_miss({"fp": q["fp"], "cls": q["cls"], "err": normalize_error(err)}))
        return 2
    print(f"# home {result.get('url')} pool={result.get('pool')}")
    for i, row in enumerate(result.get("claims") or []):
        extra = f" age={row.get('age_days')} src={row.get('src')}"
        print(f"# hit {i} sim={row.get('sim')} id={row.get('id')} st={row.get('st')}{extra}")
        if row.get("warn"):
            print("# warn " + "; ".join(row["warn"]))
        print(f"fix.k {row.get('fix', {}).get('k')}\nfix.b {row.get('fix', {}).get('b')}\neval {row.get('eval', {}).get('cmd')}\n")
    return 0


def cmd_home_push(ns: argparse.Namespace) -> int:
    from .home import HomeError, publish_home

    store = _store(ns)
    c = store.get(ns.id)
    if not c:
        print("missing", file=sys.stderr)
        return 1
    try:
        result = publish_home(c, api=ns.api, token=ns.token)
    except HomeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    store.log("home-push", resolve_owner(ns.own), c.id)
    print(json.dumps(result, default=str))
    return 0


def cmd_home_propose(ns: argparse.Namespace) -> int:
    from .home import propose_line

    c = _store(ns).get(ns.id)
    if not c:
        print("missing", file=sys.stderr)
        return 1
    print(propose_line(c))
    return 0


def cmd_share(ns: argparse.Namespace) -> int:
    from .home import HomeError, share_claim, share_pending

    store = _store(ns)
    try:
        if ns.id:
            c = store.get(ns.id)
            if not c:
                print("missing", file=sys.stderr)
                return 1
            result = share_claim(store, c, api=ns.api, token=ns.token, force=ns.force)
        else:
            result = share_pending(store, api=ns.api, token=ns.token, force=ns.force)
    except HomeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(result, default=str, indent=2 if ns.fmt == "json" else None))
    return 0


def cmd_sync(ns: argparse.Namespace) -> int:
    """Pull the public commons, then submit any local claims that have not been shared."""
    from .home import HomeError, pull, share_pending

    store = _store(ns)
    out: dict = {}
    if not ns.no_pull:
        try:
            out["pull"] = pull(store, url=ns.url)
        except HomeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    try:
        out["share"] = share_pending(store, api=ns.api, token=ns.token)
    except HomeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out, default=str, indent=2 if ns.fmt == "json" else None))
    return 0


def cmd_init(ns: argparse.Namespace) -> int:
    from . import config
    from .home import HomeError, pull
    from .seed_data import materialize
    from .team import agent_slug, did_for_agent

    raw_agent = (ns.agent or os.environ.get("CLAIMIDX_AGENT") or "").strip()
    agent = agent_slug(raw_agent) if raw_agent else ""
    if ns.own:
        own = ns.own.strip()
        if not ns.agent and ":" in own:
            agent = agent_slug(own.split(":")[-1])
    elif ns.agent:
        own = did_for_agent(ns.agent)
        agent = agent_slug(ns.agent)
    else:
        own = (os.environ.get("CLAIMIDX_OWNER") or "").strip()
        if not own and not agent:
            print("error: pass --agent <any-name> or --own did:... (any runtime, any provider)", file=sys.stderr)
            return 2
        own = own or did_for_agent(agent)
    data = {"owner": own, "agent": agent, "share": True}
    if ns.home_api:
        data["home_api"] = ns.home_api
    if ns.home:
        data["home"] = ns.home
    path = config.save(data)
    os.environ["CLAIMIDX_OWNER"] = own
    os.environ["CLAIMIDX_AGENT"] = agent
    store = _store(ns)
    n = 0
    for c in materialize():
        store.put(c)
        n += 1
    pulled = None
    if not ns.offline:
        try:
            pulled = pull(store, url=ns.home)
        except HomeError as e:
            pulled = {"error": str(e)}
    hook = None
    harness = None
    if not getattr(ns, "no_hooks", False):
        from .hook import install_harness

        harness = install_harness(own=own, agent=agent)
        hook = (harness or {}).get("claude")
    print(
        json.dumps(
            {
                "config": str(path),
                "owner": own,
                "agent": agent,
                "seeded": n,
                "db": str(store.path),
                "pull": pulled,
                "whoami": whoami(own),
                "hook": hook,
                "harness": harness,
            },
            default=str,
            indent=2,
        )
    )
    return 0


def cmd_doctor(ns: argparse.Namespace) -> int:
    from . import __version__, config
    from .home import DEFAULT_LEDGER, api_url, ledger_url
    from .sandbox import replay

    store = _store(ns)
    me = whoami(getattr(ns, "own", None))
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("version", True, __version__)
    add("config", config.config_path().exists(), str(config.config_path()))
    add("identity", me["did"] not in ("did:claimidx:anon", "anon"), me["did"])
    add("roster", True, f"{me.get('agent')} listed={bool(me.get('listed'))} (optional)")
    add("db", store.path.exists(), str(store.path))
    stats = store.stats()
    add("claims", stats.get("n", 0) > 0, json.dumps(stats))
    home = ledger_url()
    try:
        from .home import fetch_ledger

        claims, skipped, target = fetch_ledger(home)
        add("home-read", True, f"{target} pool={len(claims)} skipped={len(skipped)}")
    except Exception as e:
        add("home-read", home == DEFAULT_LEDGER, f"{home}: {e}")
    api = api_url()
    if api:
        try:
            from .home import _get

            raw = _get(api.rstrip("/") + "/health").decode("utf-8", errors="replace")
            add("home-api", True, raw[:200])
        except Exception as e:
            add("home-api", False, f"{api}: {e}")
    else:
        add("home-api", True, "not set (share will write outbox / PR lines)")
    ev = replay("true", 0)
    add("eval-true", ev.held, ev.reason)
    from .hook import (
        claude_settings_path,
        cursor_mcp_path,
        grok_config_path,
        opencode_config_path,
        settings_has_claimidx,
        vscode_mcp_path,
    )

    hp = claude_settings_path()
    if hp.exists():
        try:
            hook_data = json.loads(hp.read_text(encoding="utf-8"))
            hooked = isinstance(hook_data, dict) and settings_has_claimidx(hook_data)
        except (OSError, json.JSONDecodeError):
            hooked = False
        add("claude-hook", True, f"{'installed' if hooked else 'missing PostToolUseFailure'} {hp}")
    else:
        add("claude-hook", True, f"not installed ({hp}); claimidx init writes it")
    cp = cursor_mcp_path()
    if cp.exists():
        try:
            cur = json.loads(cp.read_text(encoding="utf-8"))
            has = isinstance(cur, dict) and isinstance(cur.get("mcpServers"), dict) and "claimidx" in cur["mcpServers"]
        except (OSError, json.JSONDecodeError):
            has = False
        add("cursor-mcp", True, f"{'installed' if has else 'missing claimidx'} {cp}")
    else:
        add("cursor-mcp", True, f"skip ({cp})")
    gp = grok_config_path()
    if gp.exists():
        try:
            has = "[mcp_servers.claimidx]" in gp.read_text(encoding="utf-8")
        except OSError:
            has = False
        add("grok-mcp", True, f"{'installed' if has else 'missing claimidx'} {gp}")
    else:
        add("grok-mcp", True, f"skip ({gp})")
    oc = opencode_config_path()
    if oc.exists():
        try:
            od = json.loads(oc.read_text(encoding="utf-8"))
            has = isinstance(od, dict) and isinstance(od.get("mcp"), dict) and "claimidx" in od["mcp"]
        except (OSError, json.JSONDecodeError):
            has = False
        add("opencode-mcp", True, f"{'installed' if has else 'missing claimidx'} {oc}")
    else:
        add("opencode-mcp", True, f"skip ({oc})")
    vp = vscode_mcp_path()
    if vp.exists():
        try:
            vd = json.loads(vp.read_text(encoding="utf-8"))
            has = isinstance(vd, dict) and isinstance(vd.get("servers"), dict) and "claimidx" in vd["servers"]
        except (OSError, json.JSONDecodeError):
            has = False
        add("vscode-mcp", True, f"{'installed' if has else 'missing claimidx'} {vp}")
    else:
        add("vscode-mcp", True, f"skip ({vp})")
    ok = all(c["ok"] for c in checks)
    print(json.dumps({"ok": ok, "whoami": me, "checks": checks}, indent=2))
    return 0 if ok else 2


def cmd_events(ns: argparse.Namespace) -> int:
    rows = _store(ns).events(limit=ns.k, actor=ns.actor)
    print(json.dumps(rows, indent=2 if ns.fmt == "json" else None))
    return 0


def cmd_token(ns: argparse.Namespace) -> int:
    from . import tokens

    if ns.token_cmd == "new":
        tok = tokens.mint(ns.name)
        print(json.dumps({"name": ns.name, "token": tok, "path": str(tokens.tokens_path())}, indent=2))
        print("set CLAIMIDX_HOME_TOKEN on clients; keep this value", file=sys.stderr)
        return 0
    print(json.dumps({"path": str(tokens.tokens_path()), "n": len(tokens._load().get("tokens") or [])}))
    return 0


def _csv(v: str) -> list[str]:
    return [p.strip() for p in v.split(",") if p.strip()]


class _AppendCsv(argparse.Action):
    """Repeating --dep/--tool appends. Comma-separated values still split."""

    def __call__(self, parser, namespace, values, option_string=None):
        acc = list(getattr(namespace, self.dest) or [])
        acc.extend(_csv(values) if isinstance(values, str) else list(values))
        setattr(namespace, self.dest, acc)


class _AppendTried(argparse.Action):
    """Each --tried is one provenance sentence. Do not comma-split."""

    def __call__(self, parser, namespace, values, option_string=None):
        acc = list(getattr(namespace, self.dest) or [])
        acc.append(values.strip() if isinstance(values, str) else str(values))
        setattr(namespace, self.dest, acc)


def _glue_dashed_opt(argv: list[str], opt: str) -> list[str]:
    """`--fix-b -Dfoo` is a value, not a flag. argparse needs `--fix-b=-Dfoo`."""
    out: list[str] = []
    i = 0
    n = len(argv)
    while i < n:
        a = argv[i]
        if a == opt and i + 1 < n and argv[i + 1].startswith("-") and not argv[i + 1].startswith("--"):
            out.append(opt + "=" + argv[i + 1])
            i += 2
            continue
        out.append(a)
        i += 1
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claimidx", description="Claimidx — prior art for agents. Ask before you burn tokens.")
    p.add_argument("--db", default=None, help="sqlite path (default: $CLAIMIDX_DB or ~/.claimidx/index.sqlite)")
    p.add_argument("--fmt", choices=["dense", "json", "id"], default="dense")
    p.add_argument("--version", action="version", version=f"claimidx {__version__}")
    p.add_argument(
        "--json-errors",
        action="store_true",
        help="emit machine-readable JSON errors on stderr",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", aliases=["query"], help="retrieve prior art for a failure")
    a.add_argument("--err", required=True, help="raw failure or traceback")
    a.add_argument("--cls", help="optional failure class override")
    a.add_argument("--eco", help="ecosystem such as py, npm, go, mcp, or ci")
    a.add_argument("--rt", help="runtime and version, for example py@3.13")
    a.add_argument("--dep", action=_AppendCsv, default=None, help="dependency name@version; repeatable or comma-separated")
    a.add_argument("-k", type=int, default=5, help="maximum hits")
    a.set_defaults(func=cmd_ask)
    hk = sub.add_parser("hook", help="Harness sensor: stdin failed-tool JSON or stderr → ask. Never applies fix.b.")
    hk.add_argument("--err")
    hk.add_argument("--cls")
    hk.add_argument("--eco")
    hk.add_argument("--rt")
    hk.add_argument("--dep", action=_AppendCsv, default=None)
    hk.add_argument("-k", type=int, default=5)
    hk.add_argument("--install", action="store_true", help="Write Claude Code PostToolUseFailure into settings.json")
    hk.set_defaults(func=cmd_hook)
    pub = sub.add_parser("publish")
    pub.add_argument("--err", required=True)
    pub.add_argument("--fix-k", required=True, choices=["pin", "patch", "config", "constraint", "cmd", "wontfix"])
    pub.add_argument("--fix-b", required=True)
    pub.add_argument("--eval", required=True)
    pub.add_argument("--expect", "--expect-exit", dest="expect", type=int, default=0, help="expected evaluation process exit code")
    pub.add_argument("--cls")
    pub.add_argument("--eco")
    pub.add_argument("--rt")
    pub.add_argument("--dep", action=_AppendCsv, default=None)
    pub.add_argument("--tool", action=_AppendCsv, default=None)
    pub.add_argument("--tried", action=_AppendTried, default=None)
    pub.add_argument("--own")
    pub.add_argument("--model")
    pub.add_argument("--note")
    pub.add_argument("--force", action="store_true")
    pub.add_argument("--alternative", action="store_true", help="store a distinct remedy for an existing failure fingerprint")
    pub.add_argument("--proof", help="attach a structured v2 proof JSON document")
    pub.set_defaults(func=cmd_publish)
    c = sub.add_parser("confirm")
    c.add_argument("id")
    c.add_argument("--own")
    c.add_argument("--replay", action="store_true")
    c.add_argument("--cwd")
    c.set_defaults(func=cmd_confirm)
    sc = sub.add_parser("scan")
    sc.add_argument("--err", default="")
    sc.add_argument("--fix-k", default="constraint")
    sc.add_argument("--fix-b", default="ok")
    sc.add_argument("--eval", default="true")
    sc.add_argument("--note", default="")
    sc.set_defaults(func=cmd_scan)
    f = sub.add_parser("fail")
    f.add_argument("id")
    f.add_argument("--own")
    f.add_argument("--note", default="")
    f.set_defaults(func=cmd_fail)
    vf = sub.add_parser("verify", help="Replay evals. Default --dry-run lists claims; no evals/venv/pip. --apply to confirm/fail.")
    vf.add_argument("--id", action="append", default=None, help="claim id; repeatable")
    vf.add_argument("-k", type=int, default=8)
    vf.add_argument("--own")
    vf_mode = vf.add_mutually_exclusive_group()
    vf_mode.add_argument("--dry-run", action="store_true", dest="dry_run", help="list chosen claims; do not run evals, venv, or pip (default)")
    vf_mode.add_argument("--apply", action="store_false", dest="dry_run", help="run evals; confirm if held, fail on a proven miss")
    vf.add_argument("--ledger", help="optional public jsonl to project nc/nf/st into")
    vf.add_argument("--runnable", action="store_true", help="only self-contained python -c evals; confirm or fail, do not pick tree recipes")
    vf.add_argument(
        "--harness",
        action="store_true",
        help="two-state pin replay: confirm only if unpinned misses and pin holds; skip if the eval cannot prove the pin; fail only on a proven pin miss",
    )
    vf.add_argument("--cwd", help="working directory for tree-scoped evals (default: isolated scratch)")
    vf.set_defaults(func=cmd_verify, dry_run=True)
    rj = sub.add_parser("reject")
    rj.add_argument("id")
    rj.add_argument("--own")
    rj.set_defaults(func=cmd_reject)
    s = sub.add_parser("show")
    s.add_argument("id")
    s.set_defaults(func=cmd_show)
    explain = sub.add_parser("explain", help="show the v2 failure/remedy/proof/observation graph for a v1 claim")
    explain.add_argument("id")
    explain.set_defaults(func=cmd_explain)
    explain_policy = sub.add_parser("explain-policy", help="show executable-proof admission policy")
    explain_policy.set_defaults(func=cmd_explain_policy)
    proof = sub.add_parser("proof", help="create, validate, or run a structured v2 proof")
    proof_sub = proof.add_subparsers(dest="proof_cmd", required=True)
    proof_create = proof_sub.add_parser("create", help="create a shell-free proof document")
    proof_create.add_argument("--program", required=True)
    proof_create.add_argument("--arg", action="append", default=[])
    proof_create.add_argument("--expect", "--expect-exit", dest="expect", type=int, default=0)
    proof_create.add_argument("--output")
    proof_create.set_defaults(func=cmd_proof)
    proof_validate = proof_sub.add_parser("validate")
    proof_validate.add_argument("path")
    proof_validate.set_defaults(func=cmd_proof)
    proof_run = proof_sub.add_parser("run")
    proof_run.add_argument("path")
    proof_run.add_argument("--cwd")
    proof_run.set_defaults(func=cmd_proof)
    identity = sub.add_parser("identity", help="create and use optional Ed25519 did:key identity")
    identity_sub = identity.add_subparsers(dest="identity_cmd", required=True)
    identity_keygen = identity_sub.add_parser("keygen")
    identity_keygen.add_argument("--key")
    identity_keygen.add_argument("--force", action="store_true")
    identity_keygen.set_defaults(func=cmd_identity)
    identity_show = identity_sub.add_parser("show")
    identity_show.add_argument("--key")
    identity_show.set_defaults(func=cmd_identity)
    identity_sign = identity_sub.add_parser("sign")
    identity_sign.add_argument("record")
    identity_sign.add_argument("--key")
    identity_sign.set_defaults(func=cmd_identity)
    identity_verify = identity_sub.add_parser("verify")
    identity_verify.add_argument("record")
    identity_verify.add_argument("--key")
    identity_verify.set_defaults(func=cmd_identity)
    ls = sub.add_parser("ls")
    ls.add_argument("--st")
    ls.add_argument("--eco")
    ls.add_argument("--own")
    ls.add_argument("-k", "--limit", dest="k", type=int, default=50, help="max rows (also --limit)")
    ls.set_defaults(func=cmd_ls)
    fp = sub.add_parser("fp")
    fp.add_argument("--err", required=True)
    fp.add_argument("--cls")
    fp.add_argument("--eco")
    fp.add_argument("--rt")
    fp.add_argument("--dep", action=_AppendCsv, default=None)
    fp.set_defaults(func=cmd_fp)
    st = sub.add_parser("stats")
    st.set_defaults(func=cmd_stats)
    sd = sub.add_parser("seed")
    sd.add_argument("--path")
    sd.set_defaults(func=cmd_seed)
    ex = sub.add_parser("export")
    ex.add_argument("path")
    ex.set_defaults(func=cmd_export)
    sv = sub.add_parser("serve")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=7340)
    sv.set_defaults(func=cmd_serve)
    w = sub.add_parser("whoami")
    w.add_argument("--own")
    w.set_defaults(func=cmd_whoami)
    tm = sub.add_parser("team")
    tm.set_defaults(func=cmd_team)
    ing = sub.add_parser("ingest")
    ing.add_argument("--err", required=True)
    ing.add_argument("--fix-k", required=True, choices=["pin", "patch", "config", "constraint", "cmd", "wontfix"])
    ing.add_argument("--fix-b", required=True)
    ing.add_argument("--eval", required=True)
    ing.add_argument("--expect", "--expect-exit", dest="expect", type=int, default=0, help="expected evaluation process exit code")
    ing.add_argument("--cls")
    ing.add_argument("--eco")
    ing.add_argument("--rt")
    ing.add_argument("--dep", action=_AppendCsv, default=None)
    ing.add_argument("--tool", action=_AppendCsv, default=None)
    ing.add_argument("--tried", action=_AppendTried, default=None)
    ing.add_argument("--own")
    ing.add_argument("--model")
    ing.add_argument("--note")
    ing.add_argument("--force", action="store_true")
    ing.add_argument("--alternative", action="store_true", help="store a distinct remedy for an existing failure fingerprint")
    ing.add_argument("--proof", help="attach a structured v2 proof JSON document")
    ing.set_defaults(func=cmd_ingest)
    hp = sub.add_parser("home-pull")
    hp.add_argument("--url")
    hp.set_defaults(func=cmd_home_pull)
    ha = sub.add_parser("home-ask")
    ha.add_argument("--err", required=True)
    ha.add_argument("--cls")
    ha.add_argument("--eco")
    ha.add_argument("--rt")
    ha.add_argument("--dep", action=_AppendCsv, default=None)
    ha.add_argument("--url")
    ha.add_argument("-k", type=int, default=5)
    ha.set_defaults(func=cmd_home_ask)
    hpush = sub.add_parser("home-push")
    hpush.add_argument("id")
    hpush.add_argument("--api")
    hpush.add_argument("--token")
    hpush.add_argument("--own")
    hpush.set_defaults(func=cmd_home_push)
    hprop = sub.add_parser("home-propose")
    hprop.add_argument("id")
    hprop.set_defaults(func=cmd_home_propose)
    sh = sub.add_parser("share", help="Submit local claims to the commons (live home, else outbox)")
    sh.add_argument("id", nargs="?", help="claim id; omit to share every unshared local claim")
    sh.add_argument("--api")
    sh.add_argument("--token")
    sh.add_argument("--force", action="store_true")
    sh.set_defaults(func=cmd_share)
    sy = sub.add_parser("sync", help="Pull the public ledger, then share unshared local claims")
    sy.add_argument("--url")
    sy.add_argument("--api")
    sy.add_argument("--token")
    sy.add_argument("--no-pull", action="store_true")
    sy.set_defaults(func=cmd_sync)
    ini = sub.add_parser("init", help="Write ~/.claimidx/config.json, seed the local index, pull home")
    ini.add_argument("--own")
    ini.add_argument("--agent")
    ini.add_argument("--home-api")
    ini.add_argument("--home")
    ini.add_argument("--offline", action="store_true")
    ini.add_argument("--no-hooks", action="store_true", help="Do not write Claude hook or harness MCP")
    ini.set_defaults(func=cmd_init)
    evs = sub.add_parser("events", help="Audit log of ask/publish/confirm/share")
    evs.add_argument("-k", type=int, default=50)
    evs.add_argument("--actor")
    evs.set_defaults(func=cmd_events)
    doc = sub.add_parser("doctor", help="Check identity, index, home, and eval sandbox")
    doc.set_defaults(func=cmd_doctor)
    tok = sub.add_parser("token", help="Mint write tokens for a private home")
    tok_sub = tok.add_subparsers(dest="token_cmd")
    tok_new = tok_sub.add_parser("new")
    tok_new.add_argument("--name", required=True)
    tok_new.set_defaults(func=cmd_token)
    tok_ls = tok_sub.add_parser("ls")
    tok_ls.set_defaults(func=cmd_token)
    tok.set_defaults(func=cmd_token, token_cmd="ls", name="")
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows redirected streams often inherit a legacy code page.  Claimidx's
    # CLI and help contain Unicode, so make output deterministic for agents.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass
    raw = list(argv) if argv is not None else sys.argv[1:]
    raw = _glue_dashed_opt(raw, "--fix-b")
    ns = build_parser().parse_args(raw)
    try:
        return int(ns.func(ns))
    except BrokenPipeError:
        return 0
    except OSError as e:
        if getattr(e, "errno", None) == 32:
            return 0
        print(f"error: {e}", file=sys.stderr)
        return 1
    except (PolicyError, SecretError) as e:
        _print_cli_error(ns, e)
        return 2
    except KeyError as e:
        print(f"missing {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
