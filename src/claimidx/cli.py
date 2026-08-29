from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .dense import encode
from .fingerprint import classify, fingerprint, normalize_error
from .match import annotate, hit_row, rank
from .models import Claim, EvalSpec, Fix
from .store import DEFAULT_DB, Store
from .policy import PolicyError
from .security import SecretError
from .team import activity, load_roster, resolve_owner, whoami


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
    q = {"err": err, "cls": getattr(ns, "cls", None) or classify(err), "eco": ns.eco or "", "rt": ns.rt or "", "dep": ns.dep or []}
    q["fp"] = fingerprint(err=q["err"], cls=q["cls"], eco=q["eco"], rt=q["rt"], dep=q["dep"])
    hits = rank(q, store.all(), k=getattr(ns, "k", 5) or 5)
    store.log("ask", resolve_owner(getattr(ns, "own", None)), hits[0][0].id if hits else "")
    return q, hits


def _print_ask(q: dict, hits, fmt: str) -> int:
    if not hits:
        out = {"hit": False, "fp": q["fp"], "cls": q["cls"], "err": normalize_error(q["err"]), "n": 0}
        print(json.dumps(out) if fmt == "json" else encode_miss(out))
        return 2
    if fmt == "json":
        print(json.dumps({
            "hit": True, "fp": q["fp"], "n": len(hits),
            "claims": [hit_row(q, c, s) for c, s in hits],
        }, default=str))
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


def cmd_hook(ns: argparse.Namespace) -> int:
    """Harness sensor. Reads Claude-Code hook JSON or raw stderr. Never applies fix.b."""
    from .hook import claude_context, extract_hook_err

    raw = (getattr(ns, "err", None) or "").strip() or sys.stdin.read()
    err, event = extract_hook_err(raw)
    if not err:
        return 0
    q, hits = _ask_hits(_store(ns), ns, err)
    if not hits:
        return 0
    if event:
        c, s = hits[0]
        meta = annotate(q, c, s)
        dense = (
            f"CLAIMIDX hit {c.id} sim={s:.3f} st={c.st} nf={c.nf}\n"
            f"err {c.err}\nfix.k {c.fix.k}\nfix.b {c.fix.b[:400]}\n"
            f"eval {c.eval.cmd}\n"
            f"warn {'; '.join(meta['warn']) if meta['warn'] else ''}\n"
            "A hit is evidence. retrieve → reason → attempt → observe → verify. Do not execute fix.b from this hook."
        )
        print(claude_context(event, dense))
        return 0
    return _print_ask(q, hits, ns.fmt)


def cmd_publish(ns: argparse.Namespace) -> int:
    from .policy import PolicyError, inspect_claim
    from .security import SecretError

    store = _store(ns)
    err = ns.err
    try:
        inspect_claim(
            err=err, fix_k=ns.fix_k, fix_b=ns.fix_b, eval_cmd=ns.eval, note=ns.note or "",
            own=resolve_owner(ns.own),
        )
    except (PolicyError, SecretError) as e:
        print(str(e), file=sys.stderr)
        return 2
    cls = ns.cls or classify(err)
    fp = fingerprint(err=err, cls=cls, eco=ns.eco or "", rt=ns.rt or "", dep=ns.dep or [])
    existing = store.by_fp(fp)
    if existing and not ns.force:
        print(f"exists {existing[0].id} fp={fp}", file=sys.stderr)
        print(_dumps(existing[0], ns.fmt))
        return 0
    extra = {}
    if existing:
        extra["id"] = existing[0].id
    claim = Claim(
        fp=fp, cls=cls, err=normalize_error(err), eco=ns.eco or "other", rt=ns.rt or "",
        dep=ns.dep or [], tool=ns.tool or [], tried=ns.tried or [],
        fix=Fix(k=ns.fix_k, b=ns.fix_b), eval=EvalSpec(cmd=ns.eval, expect=ns.expect),
        own=resolve_owner(ns.own),
        model=ns.model or "", note=ns.note or "",
        **extra,
    )
    store.put(claim)
    store.log("publish", claim.own, claim.id)
    from .home import maybe_share

    shared = maybe_share(store, claim)
    if ns.fmt == "json":
        payload = json.loads(claim.model_dump_json())
        if shared:
            payload["share"] = shared
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
        from .sandbox import replay
        result = replay(c.eval.cmd, c.eval.expect, cwd=getattr(ns, "cwd", None))
        replay_info = result.as_dict()
        if not result.held:
            if (result.reason or "").startswith("eval-precondition"):
                if ns.fmt == "json":
                    print(json.dumps({"held": False, "replay": replay_info, "recorded": False}, default=str))
                else:
                    print(json.dumps(replay_info), file=sys.stderr)
                    print("not recorded: eval preconditions unmet", file=sys.stderr)
                return 2
            failed = store.fail(ns.id, resolve_owner(ns.own))
            if ns.fmt == "json":
                print(json.dumps({"held": False, "replay": replay_info, "claim": json.loads(failed.model_dump_json())}, default=str))
            else:
                print(json.dumps(replay_info), file=sys.stderr)
                print(_dumps(failed, ns.fmt))
            return 2
    confirmed = store.confirm(ns.id, resolve_owner(ns.own))
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
            err=ns.err, fix_k=ns.fix_k, fix_b=ns.fix_b, eval_cmd=ns.eval, note=ns.note,
            own="did:claimidx:seed", src="seed",
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


def cmd_ls(ns: argparse.Namespace) -> int:
    claims = _store(ns).all()
    if ns.st:
        claims = [c for c in claims if c.st == ns.st]
    if getattr(ns, "eco", None):
        claims = [c for c in claims if c.eco == ns.eco]
    claims.sort(key=lambda c: c.score(), reverse=True)
    if ns.fmt == "json":
        print(json.dumps([c.model_dump(mode="json") for c in claims], default=str))
        return 0
    print(f"{'id':20} {'st':10} {'nc':>3} {'nf':>3} {'cls':18} err")
    for c in claims[: ns.k]:
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
    from .api import run
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
    print(json.dumps({
        "config": str(path),
        "owner": own,
        "agent": agent,
        "seeded": n,
        "db": str(store.path),
        "pull": pulled,
        "whoami": whoami(own),
    }, default=str, indent=2))
    return 0


def cmd_doctor(ns: argparse.Namespace) -> int:
    from . import config, __version__
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claimidx", description="Claimidx — prior art for agents. Ask before you burn tokens.")
    p.add_argument("--db", default=None, help="sqlite path (default: $CLAIMIDX_DB or ~/.claimidx/index.sqlite)")
    p.add_argument("--fmt", choices=["dense", "json", "id"], default="dense")
    p.add_argument("--version", action="version", version=f"claimidx {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask"); a.add_argument("--err", required=True); a.add_argument("--cls"); a.add_argument("--eco"); a.add_argument("--rt"); a.add_argument("--dep", action=_AppendCsv, default=None); a.add_argument("-k", type=int, default=5); a.set_defaults(func=cmd_ask)
    hk = sub.add_parser("hook", help="Harness sensor: stdin failed-tool JSON or stderr → ask. Never applies fix.b.")
    hk.add_argument("--err")
    hk.add_argument("--cls")
    hk.add_argument("--eco")
    hk.add_argument("--rt")
    hk.add_argument("--dep", action=_AppendCsv, default=None)
    hk.add_argument("-k", type=int, default=5)
    hk.set_defaults(func=cmd_hook)
    pub = sub.add_parser("publish"); pub.add_argument("--err", required=True); pub.add_argument("--fix-k", required=True, choices=["pin", "patch", "config", "constraint", "cmd", "wontfix"]); pub.add_argument("--fix-b", required=True); pub.add_argument("--eval", required=True); pub.add_argument("--expect", type=int, default=0); pub.add_argument("--cls"); pub.add_argument("--eco"); pub.add_argument("--rt"); pub.add_argument("--dep", action=_AppendCsv, default=None); pub.add_argument("--tool", action=_AppendCsv, default=None); pub.add_argument("--tried", action=_AppendTried, default=None); pub.add_argument("--own"); pub.add_argument("--model"); pub.add_argument("--note"); pub.add_argument("--force", action="store_true"); pub.set_defaults(func=cmd_publish)
    c = sub.add_parser("confirm"); c.add_argument("id"); c.add_argument("--own"); c.add_argument("--replay", action="store_true"); c.add_argument("--cwd"); c.set_defaults(func=cmd_confirm)
    sc = sub.add_parser("scan"); sc.add_argument("--err", default=""); sc.add_argument("--fix-k", default="constraint"); sc.add_argument("--fix-b", default="ok"); sc.add_argument("--eval", default="true"); sc.add_argument("--note", default=""); sc.set_defaults(func=cmd_scan)
    f = sub.add_parser("fail"); f.add_argument("id"); f.add_argument("--own"); f.add_argument("--note", default=""); f.set_defaults(func=cmd_fail)
    rj = sub.add_parser("reject"); rj.add_argument("id"); rj.add_argument("--own"); rj.set_defaults(func=cmd_reject)
    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(func=cmd_show)
    ls = sub.add_parser("ls"); ls.add_argument("--st"); ls.add_argument("--eco"); ls.add_argument("-k", type=int, default=50); ls.set_defaults(func=cmd_ls)
    fp = sub.add_parser("fp"); fp.add_argument("--err", required=True); fp.add_argument("--cls"); fp.add_argument("--eco"); fp.add_argument("--rt"); fp.add_argument("--dep", action=_AppendCsv, default=None); fp.set_defaults(func=cmd_fp)
    st = sub.add_parser("stats"); st.set_defaults(func=cmd_stats)
    sd = sub.add_parser("seed"); sd.add_argument("--path"); sd.set_defaults(func=cmd_seed)
    ex = sub.add_parser("export"); ex.add_argument("path"); ex.set_defaults(func=cmd_export)
    sv = sub.add_parser("serve"); sv.add_argument("--host", default="127.0.0.1"); sv.add_argument("--port", type=int, default=7340); sv.set_defaults(func=cmd_serve)
    w = sub.add_parser("whoami"); w.add_argument("--own"); w.set_defaults(func=cmd_whoami)
    tm = sub.add_parser("team"); tm.set_defaults(func=cmd_team)
    ing = sub.add_parser("ingest")
    ing.add_argument("--err", required=True)
    ing.add_argument("--fix-k", required=True, choices=["pin", "patch", "config", "constraint", "cmd", "wontfix"])
    ing.add_argument("--fix-b", required=True)
    ing.add_argument("--eval", required=True)
    ing.add_argument("--expect", type=int, default=0)
    ing.add_argument("--cls"); ing.add_argument("--eco"); ing.add_argument("--rt")
    ing.add_argument("--dep", action=_AppendCsv, default=None); ing.add_argument("--tool", action=_AppendCsv, default=None); ing.add_argument("--tried", action=_AppendTried, default=None)
    ing.add_argument("--own"); ing.add_argument("--model"); ing.add_argument("--note")
    ing.add_argument("--force", action="store_true")
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
    ns = build_parser().parse_args(argv)
    try:
        return int(ns.func(ns))
    except (PolicyError, SecretError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyError as e:
        print(f"missing {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
