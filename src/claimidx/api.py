from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from pydantic import BaseModel, Field

from . import __version__
from . import tokens as home_tokens
from .dense import encode
from .discovery import LINK_HEADER, ROOT, ROUTES
from .discovery import resolve as resolve_discovery
from .fingerprint import classify, fingerprint, normalize_error
from .match import hit_row, rank
from .models import Claim, EvalSpec, Fix
from .policy import PolicyError, require_identity
from .public import refine_eval
from .security import SecretError
from .store import Store, force_reset_emits, force_reset_from
from .team import resolve_owner
from .team import whoami as team_whoami

WEB = Path(__file__).resolve().parents[2] / "web" / "index.html"


def _stripe_hook():
    """Stripe webhook lives in git extras/, not the pip wheel."""
    try:
        from .stripe_hook import WebhookError, handle_payload

        return WebhookError, handle_payload
    except ImportError:
        pass
    extra = ROOT / "extras" / "stripe_hook.py"
    if not extra.is_file():
        return None
    import importlib.util

    spec = importlib.util.spec_from_file_location("_claimidx_stripe_hook", extra)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.WebhookError, mod.handle_payload


class AskBody(BaseModel):
    err: str
    cls: str | None = None
    eco: str | None = None
    rt: str | None = None
    dep: list[str] = Field(default_factory=list)
    k: int = 5


class PublishBody(BaseModel):
    err: str
    fix_k: str
    fix_b: str
    eval: str
    expect: int = 0
    cls: str | None = None
    eco: str = "other"
    rt: str = ""
    dep: list[str] = Field(default_factory=list)
    tool: list[str] = Field(default_factory=list)
    tried: list[str] = Field(default_factory=list)
    own: str = ""
    model: str = ""
    note: str = ""
    force: bool = False
    id: str = ""


def _bearer(authorization: str | None) -> str:
    raw = (authorization or "").strip()
    if raw.lower().startswith("bearer "):
        return raw[7:].strip()
    return raw


def require_write(authorization: str | None = Header(default=None)) -> str:
    """If the operator configured tokens, refuse anonymous writes."""
    presented = _bearer(authorization)
    if not home_tokens.write_protection_enabled():
        return presented
    if not home_tokens.valid(presented):
        raise HTTPException(401, "home token required")
    return presented


def create_app(db: str | None = None) -> FastAPI:
    store = Store(db)
    origins = [o.strip() for o in (os.environ.get("CLAIMIDX_CORS") or "").split(",") if o.strip()]
    app = FastAPI(
        title="Claimidx",
        version=__version__,
        description="Prior art for AI agents. Ask before you retry a failure. Ingest after you learn. Share so the next agent does not pay twice.",
        docs_url="/api/docs",
    )
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def agent_link_header(request: Request, call_next):
        resp: Response = await call_next(request)
        resp.headers.setdefault("Link", LINK_HEADER)
        return resp

    def _mount_discovery(url_path: str, request: Request | None = None):
        spec = resolve_discovery(url_path)
        if not spec:
            raise HTTPException(404, "missing")
        path, media = spec
        live = url_path in (
            "/.well-known/agent-card.json",
            "/.well-known/agent.json",
            "/.well-known/mcp/server-card.json",
            "/.well-known/mcp.json",
        )
        if live and request is not None:
            data = json.loads(path.read_text(encoding="utf-8"))
            base = str(request.base_url).rstrip("/")
            data["url"] = base
            if isinstance(data.get("supportedInterfaces"), list):
                data["supportedInterfaces"] = [
                    {
                        "url": base,
                        "protocolBinding": "HTTP+JSON",
                        "protocolVersion": "0.3.0",
                    }
                ]
            if isinstance(data.get("serverInfo"), dict):
                data.setdefault("websiteUrl", base)
            return JSONResponse(data, media_type=media)
        return FileResponse(path, media_type=media)

    for _path in ROUTES:

        def _serve(request: Request, p: str = _path):
            return _mount_discovery(p, request)

        _serve.__name__ = "disc_" + (_path.strip("/").replace("/", "_").replace(".", "_") or "root")
        app.add_api_route(_path, _serve, methods=["GET"], include_in_schema=False)

    @app.get("/", response_class=HTMLResponse)
    def inspector():
        return WEB.read_text() if WEB.exists() else HTMLResponse("<pre>Claimidx inspector missing</pre>")

    @app.get("/health")
    def health():
        return {"ok": True, **store.stats()}

    @app.get("/api/stats")
    def stats():
        return store.stats()

    @app.get("/api/whoami")
    def api_whoami():
        return team_whoami()

    @app.get("/api/events")
    def api_events(limit: int = 50, actor: str | None = None):
        return store.events(limit=limit, actor=actor)

    @app.get("/ledger.jsonl", response_class=PlainTextResponse)
    def ledger():
        lines = []
        for c in store.all():
            if c.st == "rejected":
                continue
            payload = json.loads(c.model_dump_json())
            lines.append(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        return PlainTextResponse("\n".join(lines) + ("\n" if lines else ""), media_type="application/x-ndjson")

    @app.get("/api/claims")
    def list_claims(st: str | None = None, limit: int = 100, fmt: str = "json"):
        claims = store.all()
        if st:
            claims = [c for c in claims if c.st == st]
        claims.sort(key=lambda c: c.score(), reverse=True)
        claims = claims[:limit]
        if fmt == "dense":
            return PlainTextResponse("\n".join(encode(c) for c in claims))
        return [c.model_dump(mode="json") for c in claims]

    @app.get("/api/claims/{claim_id}")
    def get_claim(claim_id: str, fmt: str = "json"):
        c = store.get(claim_id)
        if not c:
            raise HTTPException(404, "missing")
        return PlainTextResponse(encode(c)) if fmt == "dense" else c.model_dump(mode="json")

    @app.post("/api/ask")
    def ask(payload: AskBody):
        cls = payload.cls or classify(payload.err)
        fp = fingerprint(err=payload.err, cls=cls, eco=payload.eco or "", rt=payload.rt or "", dep=payload.dep)
        q = {"err": payload.err, "cls": cls, "eco": payload.eco or "", "rt": payload.rt or "", "dep": payload.dep, "fp": fp}
        hits = rank(q, store.all(), k=payload.k)
        store.log("ask", resolve_owner(None), hits[0][0].id if hits else "")
        return {
            "hit": bool(hits),
            "fp": fp,
            "cls": cls,
            "err": normalize_error(payload.err),
            "n": len(hits),
            "claims": [hit_row(q, c, s) for c, s in hits],
        }

    @app.post("/api/publish")
    def publish(payload: PublishBody, _auth: str = Depends(require_write)):
        own = payload.own.strip() if payload.own else resolve_owner(None)
        try:
            require_identity(own, src="local")
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        if payload.force:
            cls, fp, existing = store.match_amend(
                err=payload.err,
                cls=payload.cls,
                eco=payload.eco or "",
                rt=payload.rt or "",
                dep=payload.dep,
            )
        else:
            cls = payload.cls or classify(payload.err)
            fp = fingerprint(err=payload.err, cls=cls, eco=payload.eco, rt=payload.rt, dep=payload.dep)
            existing = store.by_fp(fp)
        if existing and not payload.force:
            return {"exists": True, "claim": existing[0].model_dump(mode="json")}
        extra = {}
        reset = {}
        cid = (payload.id or "").strip()
        if cid:
            by_id = store.get(cid)
            if by_id and by_id.fp != fp and not payload.force:
                raise HTTPException(409, "id exists under a different fingerprint")
            extra["id"] = cid
            if by_id and payload.force:
                reset = force_reset_from(by_id)
        elif existing:
            extra["id"] = existing[0].id
            if payload.force:
                reset = force_reset_from(existing[0])
        try:
            c = Claim(
                fp=fp,
                cls=cls,
                err=normalize_error(payload.err),
                eco=payload.eco,
                rt=payload.rt,
                dep=payload.dep,
                tool=payload.tool,
                tried=payload.tried,
                fix=Fix(k=payload.fix_k, b=payload.fix_b),  # type: ignore[arg-type]
                eval=EvalSpec(cmd=refine_eval(payload.eval, fix_k=payload.fix_k, fix_b=payload.fix_b, dep=payload.dep, eco=payload.eco), expect=payload.expect),
                own=own,
                model=payload.model,
                note=payload.note,
                src="home",
                **extra,
            )
            store.publish(c, own, reset)
        except (PolicyError, SecretError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        body = {"exists": False, "claim": c.model_dump(mode="json")}
        if force_reset_emits(reset):
            body["force_reset"] = reset
        return body

    @app.post("/api/ingest")
    def ingest(payload: PublishBody, _auth: str = Depends(require_write)):
        return publish(payload, _auth)

    @app.post("/api/claims/{claim_id}/confirm")
    def confirm(claim_id: str, own: str = Query(""), replay: bool = Query(False), _auth: str = Depends(require_write)):
        c = store.get(claim_id)
        if not c:
            raise HTTPException(404, "missing")
        if getattr(c, "src", "local") == "home" and not replay:
            raise HTTPException(409, "quarantine: home claims require confirm ?replay=true")
        actor = resolve_owner(own or None)
        try:
            require_identity(actor)
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        # Never run eval.cmd in the home process. Replay is `claimidx confirm --replay` on the agent.
        confirmed = store.confirm(claim_id, actor, replayed=bool(replay))
        if replay:
            return {"held": True, "recorded": True, "claim": confirmed.model_dump(mode="json")}
        return confirmed.model_dump(mode="json")

    @app.post("/api/claims/{claim_id}/fail")
    def fail(claim_id: str, own: str = Query(""), _auth: str = Depends(require_write)):
        if not store.get(claim_id):
            raise HTTPException(404, "missing")
        actor = resolve_owner(own or None)
        try:
            require_identity(actor)
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        return store.fail(claim_id, actor).model_dump(mode="json")

    @app.post("/api/claims/{claim_id}/reject")
    def reject(claim_id: str, own: str = Query(""), _auth: str = Depends(require_write)):
        if not store.get(claim_id):
            raise HTTPException(404, "missing")
        actor = resolve_owner(own or None)
        try:
            require_identity(actor)
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        return store.reject(claim_id, actor).model_dump(mode="json")

    @app.post("/api/stripe/webhook")
    async def stripe_webhook(request: Request):
        secret = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()
        if not secret:
            raise HTTPException(503, "webhook secret not configured")
        hook = _stripe_hook()
        if hook is None:
            raise HTTPException(503, "webhook module not installed")
        WebhookError, handle_payload = hook
        payload = await request.body()
        header = request.headers.get("stripe-signature") or request.headers.get("Stripe-Signature") or ""
        try:
            result = handle_payload(payload, header, secret)
        except WebhookError as e:
            raise HTTPException(400, str(e)) from e
        paid = result.get("paid")
        if paid:
            store.log("stripe", paid.get("customer_email") or paid.get("id") or "", str(paid.get("type") or ""))
        return result

    return app


def run(host: str = "127.0.0.1", port: int = 7340, db: str | None = None) -> None:
    import uvicorn

    uvicorn.run(create_app(db), host=host, port=port, log_level="info")
