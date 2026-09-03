from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
from .discovery import LINK_HEADER, ROUTES
from .discovery import resolve as resolve_discovery
from .fingerprint import classify, fingerprint, normalize_error
from .graph import Bundle, ProtocolEvent
from .match import hit_row
from .models import Claim, EvalSpec, Fix
from .policy import PolicyError, require_identity
from .public import refine_eval
from .security import SecretError
from .store import Store, force_reset_emits, force_reset_from
from .team import activity
from .team import whoami as team_whoami

WEB = Path(__file__).resolve().parents[2] / "web" / "index.html"


class AskBody(BaseModel):
    err: str
    cls: str | None = None
    eco: str | None = None
    rt: str | None = None
    dep: list[str] = Field(default_factory=list)
    k: int = 5
    own: str = ""


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
    alternative: bool = False
    id: str = ""
    proof: dict[str, Any] | None = None


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
        """The home is Claimidx, not the process operator DID."""
        return {
            "home": True,
            "product": "Claimidx",
            "operator": team_whoami(),
            "actors": activity(store),
        }

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

    @app.get("/api/v2/claims/{claim_id}")
    def get_claim_graph(claim_id: str):
        graph = store.graph(claim_id)
        if not graph:
            raise HTTPException(404, "missing")
        return graph

    @app.get("/api/v2/claims/{claim_id}/projection")
    def get_projection_preview(claim_id: str):
        from .public import projection_preview

        claim = store.get(claim_id)
        if not claim:
            raise HTTPException(404, "missing")
        return projection_preview(claim)

    @app.get("/api/v2/failures/{fp_v1}")
    def get_failure_graph(fp_v1: str):
        graph = store.failure_graph(fp_v1)
        if not graph:
            raise HTTPException(404, "missing")
        return graph

    @app.post("/api/v2/bundles")
    def publish_v2_bundle(bundle: Bundle, _auth: str = Depends(require_write)):
        try:
            require_identity(bundle.remedy.own)
            store.publish_bundle(bundle)
        except (PolicyError, SecretError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        return {"accepted": True, "identity": "verified" if bundle.remedy.signature else "asserted", "remedy_id": bundle.remedy.id}

    @app.get("/api/v2/events")
    def export_v2_events(after: int = 0, limit: int = 500):
        return store.protocol_events(after=after, limit=limit)

    @app.post("/api/v2/events")
    def import_v2_events(events: list[ProtocolEvent], _auth: str = Depends(require_write)):
        for event in events:
            try:
                require_identity(event.actor)
            except PolicyError as e:
                raise HTTPException(403, str(e)) from e
        return store.import_protocol_events(events)

    @app.post("/api/ask")
    def ask(payload: AskBody):
        cls = payload.cls or classify(payload.err)
        fp = fingerprint(err=payload.err, cls=cls, eco=payload.eco or "", rt=payload.rt or "", dep=payload.dep)
        q = {"err": payload.err, "cls": cls, "eco": payload.eco or "", "rt": payload.rt or "", "dep": payload.dep, "fp": fp}
        from .query import retrieve

        caller = (payload.own or "").strip() or "did:claimidx:anon"
        hits = retrieve(store, q, k=payload.k, actor=caller)
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
        own = (payload.own or "").strip()
        if not own:
            raise HTTPException(400, "write needs a DID (own). The home operator is not the caller.")
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
        if existing and not payload.force and not payload.alternative:
            return {"exists": True, "claim": existing[0].model_dump(mode="json")}
        extra: dict[str, Any] = {}
        reset = {}
        cid = (payload.id or "").strip()
        if cid:
            by_id = store.get(cid)
            if by_id and payload.alternative:
                raise HTTPException(409, "alternative remedy requires a new claim id")
            if by_id and by_id.fp != fp and not payload.force:
                raise HTTPException(409, "id exists under a different fingerprint")
            extra["id"] = cid
            if by_id and payload.force:
                reset = force_reset_from(by_id)
        elif existing and payload.force:
            extra["id"] = existing[0].id
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
            if payload.proof:
                from .graph import Proof

                store.attach_proof(c.id, Proof.model_validate(payload.proof))
            if existing and payload.alternative:
                from .graph import Relation

                new_graph = store.graph(c.id)
                old_graph = store.graph(existing[0].id)
                if new_graph and old_graph:
                    store.add_relation(
                        Relation(
                            source_id=new_graph["remedy"]["id"],
                            target_id=old_graph["remedy"]["id"],
                            kind="alternative",
                            actor=own,
                        )
                    )
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
    def confirm(
        claim_id: str,
        own: str = Query(""),
        replay: bool = Query(False),
        trust_domain: str = Query("", max_length=200),
        sensor_plane: str = Query("", max_length=200),
        _auth: str = Depends(require_write),
    ):
        c = store.get(claim_id)
        if not c:
            raise HTTPException(404, "missing")
        if getattr(c, "src", "local") == "home" and not replay:
            raise HTTPException(409, "quarantine: home claims require confirm ?replay=true")
        actor = (own or "").strip()
        if not actor:
            raise HTTPException(400, "write needs a DID (own). The home operator is not the caller.")
        try:
            require_identity(actor)
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        # Never run eval.cmd in the home process. Replay is `claimidx confirm --replay` on the agent.
        detail = None
        if replay or trust_domain or sensor_plane:
            detail = {
                "held": True,
                "trust_domain": trust_domain,
                "sensor_plane": sensor_plane,
            }
        confirmed = store.confirm(claim_id, actor, replayed=bool(replay), detail=detail)
        if replay:
            return {"held": True, "recorded": True, "claim": confirmed.model_dump(mode="json")}
        return confirmed.model_dump(mode="json")

    @app.post("/api/claims/{claim_id}/fail")
    def fail(claim_id: str, own: str = Query(""), _auth: str = Depends(require_write)):
        if not store.get(claim_id):
            raise HTTPException(404, "missing")
        actor = (own or "").strip()
        if not actor:
            raise HTTPException(400, "write needs a DID (own). The home operator is not the caller.")
        try:
            require_identity(actor)
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        return store.fail(claim_id, actor).model_dump(mode="json")

    @app.post("/api/claims/{claim_id}/reject")
    def reject(claim_id: str, own: str = Query(""), _auth: str = Depends(require_write)):
        if not store.get(claim_id):
            raise HTTPException(404, "missing")
        actor = (own or "").strip()
        if not actor:
            raise HTTPException(400, "write needs a DID (own). The home operator is not the caller.")
        try:
            require_identity(actor)
        except PolicyError as e:
            raise HTTPException(403, str(e)) from e
        return store.reject(claim_id, actor).model_dump(mode="json")

    return app


def run(host: str = "127.0.0.1", port: int = 7340, db: str | None = None) -> None:
    import uvicorn

    uvicorn.run(create_app(db), host=host, port=port, log_level="info")
