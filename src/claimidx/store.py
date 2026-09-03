from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC
from pathlib import Path

from pydantic import ValidationError

from .models import Claim, utcnow


def _default_db() -> Path:
    return Path.home() / ".claimidx" / "index.sqlite"


DEFAULT_DB = _default_db()


def force_reset_from(old: Claim) -> dict[str, int | str]:
    """Counters a --force overwrite discards. The new row starts at 0."""
    return {
        "nr": int(getattr(old, "nr", 0) or 0),
        "nc": int(old.nc or 0),
        "nf": int(old.nf or 0),
        "rt": (old.rt or ""),
    }


def force_reset_emits(reset: dict) -> bool:
    return bool(int(reset.get("nr") or 0) or int(reset.get("nc") or 0) or int(reset.get("nf") or 0))


class Store:
    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path else DEFAULT_DB
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fts = False
        self._init()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    def _init(self) -> None:
        with self._conn() as con:
            self._migrate_v01(con)
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY, fp TEXT NOT NULL, cls TEXT NOT NULL,
                    eco TEXT, json TEXT NOT NULL, nc INTEGER DEFAULT 0,
                    nf INTEGER DEFAULT 0, st TEXT, ts TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_fp ON claims(fp)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_cls ON claims(cls)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT, kind TEXT, actor TEXT, ts TEXT, detail TEXT
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS session_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    fp TEXT,
                    claim_id TEXT,
                    detail TEXT
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_session_id ON session_events(session_id)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, fp TEXT, json TEXT NOT NULL, ts TEXT NOT NULL
                )
                """
            )
            self._migrate_events_detail(con)
            self._init_v2(con)

    def _init_v2(self, con: sqlite3.Connection) -> None:
        """Create the additive v2 graph beside the untouched v1 claim table."""
        con.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute(
            """CREATE TABLE IF NOT EXISTS failures_v2 (
                id TEXT PRIMARY KEY, fp_v1 TEXT NOT NULL UNIQUE, family_id TEXT NOT NULL,
                cls TEXT NOT NULL, eco TEXT, json TEXT NOT NULL, created TEXT NOT NULL
            )"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_failure_family ON failures_v2(family_id)")
        con.execute(
            """CREATE TABLE IF NOT EXISTS proofs_v2 (
                id TEXT PRIMARY KEY, json TEXT NOT NULL, created TEXT NOT NULL
            )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS remedies_v2 (
                id TEXT PRIMARY KEY, failure_id TEXT NOT NULL, proof_id TEXT NOT NULL,
                legacy_claim_id TEXT, status TEXT NOT NULL, own TEXT NOT NULL,
                content_hash TEXT NOT NULL, json TEXT NOT NULL, created TEXT NOT NULL
            )"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_remedy_failure ON remedies_v2(failure_id)")
        # A v1 --force revision keeps its historical v2 remedy instead of
        # destroying it, so several remedy revisions may project to one v1 id.
        con.execute("DROP INDEX IF EXISTS idx_remedy_legacy")
        con.execute("CREATE INDEX IF NOT EXISTS idx_remedy_legacy_lookup ON remedies_v2(legacy_claim_id)")
        con.execute(
            """CREATE TABLE IF NOT EXISTS observations_v2 (
                id TEXT PRIMARY KEY, remedy_id TEXT NOT NULL, proof_id TEXT NOT NULL,
                actor TEXT NOT NULL, held INTEGER NOT NULL, json TEXT NOT NULL, created TEXT NOT NULL
            )"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_observation_remedy ON observations_v2(remedy_id)")
        con.execute(
            """CREATE TABLE IF NOT EXISTS relations_v2 (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, target_id TEXT NOT NULL,
                kind TEXT NOT NULL, json TEXT NOT NULL, created TEXT NOT NULL
            )"""
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_relation_source ON relations_v2(source_id)")
        con.execute(
            """CREATE TABLE IF NOT EXISTS protocol_events_v2 (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL, object_id TEXT, actor TEXT NOT NULL,
                json TEXT NOT NULL, created TEXT NOT NULL
            )"""
        )
        try:
            con.execute("CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(id UNINDEXED, err, cls, eco, dep)")
            self._fts = True
        except sqlite3.OperationalError:
            self._fts = False
        done = con.execute("SELECT value FROM metadata WHERE key='v2_backfill'").fetchone()
        if not done:
            rows = con.execute("SELECT json FROM claims").fetchall()
            for row in rows:
                claim = self._claim_from_json(row["json"])
                if claim:
                    self._sync_v2_claim(con, claim)
            con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('v2_backfill','1')")
        self._backfill_fts(con)
        self._backfill_protocol_events(con)

    def _backfill_fts(self, con: sqlite3.Connection) -> None:
        """Populate a newly available FTS5 table even on an already-v2 database."""
        if not self._fts or con.execute("SELECT value FROM metadata WHERE key='fts_backfill'").fetchone():
            return
        con.execute("DELETE FROM claims_fts")
        for row in con.execute("SELECT id,json FROM claims").fetchall():
            claim = self._claim_from_json(row["json"])
            if claim:
                con.execute(
                    "INSERT INTO claims_fts(id,err,cls,eco,dep) VALUES(?,?,?,?,?)",
                    (claim.id, claim.err, claim.cls, claim.eco, " ".join(claim.dep)),
                )
        con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('fts_backfill','1')")

    def _backfill_protocol_events(self, con: sqlite3.Connection) -> None:
        """Project legacy audit rows once, preserving their timestamp and payload."""
        if con.execute("SELECT value FROM metadata WHERE key='event_backfill'").fetchone():
            return
        from .graph import ProtocolEvent, stable_id

        for row in con.execute("SELECT id,claim_id,kind,actor,ts,detail FROM events ORDER BY id").fetchall():
            try:
                payload = json.loads(row["detail"]) if row["detail"] else {}
            except (TypeError, json.JSONDecodeError):
                payload = {"legacy_detail": str(row["detail"])}
            actor = str(row["actor"] or "did:claimidx:anon")
            if not actor.startswith("did:"):
                actor = "did:claimidx:anon"
            material = f"legacy-event:{row['id']}:{row['ts']}:{row['kind']}:{row['claim_id']}"
            event = ProtocolEvent(
                id=stable_id("evt_", material),
                kind=str(row["kind"] or "legacy"),
                object_id=str(row["claim_id"] or ""),
                actor=actor,
                payload=payload if isinstance(payload, dict) else {"legacy_detail": payload},
                created=row["ts"] or utcnow(),
            )
            con.execute(
                "INSERT OR IGNORE INTO protocol_events_v2(id,kind,object_id,actor,json,created) VALUES(?,?,?,?,?,?)",
                (event.id, event.kind, event.object_id, event.actor, event.model_dump_json(), event.created.isoformat()),
            )
        con.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES('event_backfill','1')")

    def _sync_v2_claim(self, con: sqlite3.Connection, claim: Claim) -> None:
        from .graph import failure_from_claim, proof_from_claim, remedy_from_claim

        failure = failure_from_claim(claim)
        proof = proof_from_claim(claim)
        remedy = remedy_from_claim(claim, failure, proof)
        con.execute(
            "INSERT OR IGNORE INTO failures_v2(id,fp_v1,family_id,cls,eco,json,created) VALUES(?,?,?,?,?,?,?)",
            (failure.id, failure.fp_v1, failure.family_id, failure.cls, failure.eco, failure.model_dump_json(), failure.created.isoformat()),
        )
        con.execute(
            "INSERT OR IGNORE INTO proofs_v2(id,json,created) VALUES(?,?,?)",
            (proof.id, proof.model_dump_json(), proof.created.isoformat()),
        )
        con.execute(
            """INSERT INTO remedies_v2(id,failure_id,proof_id,legacy_claim_id,status,own,content_hash,json,created)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, json=excluded.json""",
            (
                remedy.id,
                remedy.failure_id,
                remedy.proof_id,
                remedy.legacy_claim_id,
                remedy.status,
                remedy.own,
                remedy.content_hash,
                remedy.model_dump_json(),
                remedy.created.isoformat(),
            ),
        )
        if self._fts:
            con.execute("DELETE FROM claims_fts WHERE id=?", (claim.id,))
            con.execute(
                "INSERT INTO claims_fts(id,err,cls,eco,dep) VALUES(?,?,?,?,?)",
                (claim.id, claim.err, claim.cls, claim.eco, " ".join(claim.dep)),
            )

    def _migrate_events_detail(self, con: sqlite3.Connection) -> None:
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'").fetchone()
        if not row:
            return
        cols = {r[1] for r in con.execute("PRAGMA table_info(events)").fetchall()}
        if "detail" not in cols:
            con.execute("ALTER TABLE events ADD COLUMN detail TEXT")

    def _migrate_v01(self, con: sqlite3.Connection) -> None:
        """Lift the v0.1 denormalized claims table into json-blob v0.2+."""
        row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='claims'").fetchone()
        if not row:
            return
        cols = {r[1] for r in con.execute("PRAGMA table_info(claims)").fetchall()}
        if "json" in cols:
            return
        old = con.execute("SELECT * FROM claims").fetchall()
        con.execute("ALTER TABLE claims RENAME TO claims_v01")
        from datetime import datetime

        from .models import Claim, EvalSpec, Fix

        rebuilt: list[Claim] = []
        for r in old:
            d = dict(r)
            try:
                dep = json.loads(d.get("dep") or "[]")
                tried = json.loads(d.get("tried") or "[]")
            except json.JSONDecodeError:
                dep, tried = [], []
            ts_raw = d.get("created_at") or d.get("updated_at")
            try:
                ts = datetime.fromtimestamp(float(ts_raw), tz=UTC) if ts_raw else utcnow()
            except (TypeError, ValueError, OSError):
                ts = utcnow()
            try:
                rebuilt.append(
                    Claim(
                        id=d["id"],
                        fp=d["fp"],
                        cls=d.get("cls") or "other",
                        err=d.get("err") or "",
                        eco=d.get("eco") or "other",
                        rt=d.get("rt") or "",
                        dep=dep if isinstance(dep, list) else [],
                        tried=tried if isinstance(tried, list) else [],
                        fix=Fix(k=d.get("fix_k") or "constraint", b=d.get("fix_b") or "unknown"),
                        eval=EvalSpec(cmd=d.get("eval_cmd") or "true", expect=int(d.get("eval_expect") or 0)),
                        st=d.get("st") or "proposed",
                        nc=int(d.get("nc") or 0),
                        nf=int(d.get("nf") or 0),
                        own="did:claimidx:seed",
                        ts=ts,
                        note=d.get("note") or "",
                        src="seed",
                    )
                )
            except (ValueError, TypeError):
                continue
        con.execute(
            """
            CREATE TABLE claims (
                id TEXT PRIMARY KEY, fp TEXT NOT NULL, cls TEXT NOT NULL,
                eco TEXT, json TEXT NOT NULL, nc INTEGER DEFAULT 0,
                nf INTEGER DEFAULT 0, st TEXT, ts TEXT
            )
            """
        )
        for claim in rebuilt:
            payload = claim.model_dump_json()
            con.execute(
                "INSERT INTO claims(id, fp, cls, eco, json, nc, nf, st, ts) VALUES(?,?,?,?,?,?,?,?,?)",
                (claim.id, claim.fp, claim.cls, claim.eco, payload, claim.nc, claim.nf, claim.st, claim.ts.isoformat()),
            )

    def _prepare_write(self, claim: Claim) -> Claim:
        from .policy import inspect_claim, quarantine

        inspect_claim(
            err=claim.err,
            fix_k=claim.fix.k,
            fix_b=claim.fix.b,
            eval_cmd=claim.eval.cmd,
            note=claim.note,
            own=claim.own,
            src=getattr(claim, "src", "local") or "local",
        )
        quarantine(claim)
        claim.refresh_status()
        return claim

    def _upsert(self, con: sqlite3.Connection, claim: Claim) -> None:
        payload = claim.model_dump_json()
        con.execute(
            """
            INSERT INTO claims(id, fp, cls, eco, json, nc, nf, st, ts)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                fp=excluded.fp, cls=excluded.cls, eco=excluded.eco,
                json=excluded.json, nc=excluded.nc, nf=excluded.nf,
                st=excluded.st, ts=excluded.ts
            """,
            (claim.id, claim.fp, claim.cls, claim.eco, payload, claim.nc, claim.nf, claim.st, claim.ts.isoformat()),
        )
        self._sync_v2_claim(con, claim)

    def _insert_event(
        self,
        con: sqlite3.Connection,
        claim_id: str,
        kind: str,
        actor: str,
        detail: dict | None = None,
    ) -> None:
        blob = json.dumps(detail, default=str) if detail else None
        con.execute(
            "INSERT INTO events(claim_id, kind, actor, ts, detail) VALUES(?,?,?,?,?)",
            (claim_id, kind, actor, utcnow().isoformat(), blob),
        )
        from .graph import ProtocolEvent

        event = ProtocolEvent(kind=kind, object_id=claim_id, actor=actor or "did:claimidx:anon", payload=detail or {})
        con.execute(
            "INSERT OR IGNORE INTO protocol_events_v2(id,kind,object_id,actor,json,created) VALUES(?,?,?,?,?,?)",
            (event.id, event.kind, event.object_id, event.actor, event.model_dump_json(), event.created.isoformat()),
        )

    def put(self, claim: Claim) -> Claim:
        self._prepare_write(claim)
        with self._conn() as con:
            self._upsert(con, claim)
        return claim

    def _claim_from_json(self, blob: str) -> Claim | None:
        try:
            claim = Claim.model_validate_json(blob)
        except (ValidationError, ValueError, json.JSONDecodeError):
            return None
        claim.refresh_status()
        return claim

    def get(self, claim_id: str) -> Claim | None:
        with self._conn() as con:
            row = con.execute("SELECT json FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not row:
            return None
        return self._claim_from_json(row["json"])

    def match_amend(
        self,
        *,
        err: str,
        cls: str | None,
        eco: str,
        rt: str,
        dep: list[str] | None,
    ) -> tuple[str, str, list[Claim]]:
        """Locate the row an ingest --force should overwrite.

        Fingerprint includes cls. If --cls is omitted, classify() can move the
        row to a new class (new fp, new id). Prefer the stored cls when the
        rest of the slice matches.
        """
        from .fingerprint import classify, fingerprint, normalize_error

        eco_fp = eco or ""
        eco_store = eco or "other"
        dep = list(dep or [])
        chosen = cls or classify(err)
        fp = fingerprint(err=err, cls=chosen, eco=eco_fp, rt=rt or "", dep=dep)
        existing = self.by_fp(fp)
        if existing or cls:
            return chosen, fp, existing
        nerr = normalize_error(err)
        for c in self.all():
            if c.err == nerr and c.eco == eco_store and (c.rt or "") == (rt or "") and list(c.dep or []) == dep:
                return c.cls, c.fp, [c]
        return chosen, fp, []

    def by_fp(self, fp: str) -> list[Claim]:
        with self._conn() as con:
            rows = con.execute("SELECT json FROM claims WHERE fp=?", (fp,)).fetchall()
        return [c for r in rows if (c := self._claim_from_json(r["json"]))]

    def all(self) -> list[Claim]:
        with self._conn() as con:
            rows = con.execute("SELECT json FROM claims").fetchall()
        return [c for r in rows if (c := self._claim_from_json(r["json"]))]

    def candidates(self, *, fp: str, err: str, cls: str = "", limit: int = 256) -> list[Claim]:
        """Bounded candidate retrieval for v2-scale ledgers; exact fp always wins."""
        exact = self.by_fp(fp)
        if not self._fts:
            return exact or self.all()
        tokens = [t for t in __import__("re").findall(r"[A-Za-z0-9_@.-]{3,}", err) if t.lower() not in {"error", "exception", "traceback"}]
        ids: list[str] = [c.id for c in exact]
        if tokens:
            expression = " OR ".join('"' + t.replace('"', "") + '"' for t in list(dict.fromkeys(tokens))[:12])
            try:
                with self._conn() as con:
                    rows = con.execute("SELECT id FROM claims_fts WHERE claims_fts MATCH ? LIMIT ?", (expression, max(1, limit))).fetchall()
                ids.extend(str(row["id"]) for row in rows)
            except sqlite3.OperationalError:
                return exact or self.all()
        if len(ids) < min(16, limit) and cls:
            with self._conn() as con:
                rows = con.execute("SELECT id FROM claims WHERE cls=? LIMIT ?", (cls, max(1, limit))).fetchall()
            ids.extend(str(row["id"]) for row in rows)
        out: list[Claim] = []
        for claim_id in dict.fromkeys(ids):
            claim = self.get(claim_id)
            if claim:
                out.append(claim)
        return out

    def graph(self, claim_id: str) -> dict | None:
        claim = self.get(claim_id)
        if not claim:
            return None
        with self._conn() as con:
            remedy_row = con.execute(
                "SELECT json FROM remedies_v2 WHERE legacy_claim_id=? ORDER BY created DESC LIMIT 1",
                (claim_id,),
            ).fetchone()
            if not remedy_row:
                return None
            remedy = json.loads(remedy_row["json"])
            failure_row = con.execute("SELECT json FROM failures_v2 WHERE id=?", (remedy["failure_id"],)).fetchone()
            proof_row = con.execute("SELECT json FROM proofs_v2 WHERE id=?", (remedy["proof_id"],)).fetchone()
            observations = [
                json.loads(row["json"])
                for row in con.execute("SELECT json FROM observations_v2 WHERE remedy_id=? ORDER BY created", (remedy["id"],)).fetchall()
            ]
            relations = [
                json.loads(row["json"])
                for row in con.execute(
                    "SELECT json FROM relations_v2 WHERE source_id=? OR target_id=? ORDER BY created", (remedy["id"], remedy["id"])
                ).fetchall()
            ]
        return {
            "failure": json.loads(failure_row["json"]) if failure_row else None,
            "remedy": remedy,
            "proof": json.loads(proof_row["json"]) if proof_row else None,
            "observations": observations,
            "relations": relations,
        }

    def add_observation(self, observation) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO observations_v2(id,remedy_id,proof_id,actor,held,json,created) VALUES(?,?,?,?,?,?,?)",
                (
                    observation.id,
                    observation.remedy_id,
                    observation.proof_id,
                    observation.actor,
                    1 if observation.held else 0,
                    observation.model_dump_json(),
                    observation.created.isoformat(),
                ),
            )

    def attach_proof(self, claim_id: str, proof) -> None:
        from .graph import canonical_hash
        from .proofs import validate_proof

        validate_proof(proof)
        graph = self.graph(claim_id)
        if not graph:
            raise KeyError(claim_id)
        remedy = graph["remedy"]
        remedy["proof_id"] = proof.id
        remedy["content_hash"] = ""
        remedy["signature"] = ""
        remedy["key_id"] = ""
        remedy["content_hash"] = canonical_hash(remedy)
        with self._conn() as con:
            con.execute(
                "INSERT OR REPLACE INTO proofs_v2(id,json,created) VALUES(?,?,?)",
                (proof.id, proof.model_dump_json(), proof.created.isoformat()),
            )
            con.execute(
                "UPDATE remedies_v2 SET proof_id=?, content_hash=?, json=? WHERE id=?",
                (proof.id, remedy["content_hash"], json.dumps(remedy, separators=(",", ":"), default=str), remedy["id"]),
            )

    def publish_bundle(self, bundle) -> None:
        from .fingerprint import family_fingerprint, fingerprint
        from .graph import canonical_hash, stable_id
        from .identity import verify_record
        from .policy import inspect_claim
        from .proofs import validate_proof

        if bundle.failure.id != stable_id("flr_", bundle.failure.fp_v1):
            raise ValueError("failure id does not match fp_v1")
        expected_fp = fingerprint(
            err=bundle.failure.err,
            cls=bundle.failure.cls,
            eco=bundle.failure.eco,
            rt=bundle.failure.rt,
            dep=bundle.failure.dep,
        )
        if bundle.failure.fp_v1 != expected_fp:
            raise ValueError("failure fp_v1 does not match failure fields")
        expected_family = family_fingerprint(err=bundle.failure.err, cls=bundle.failure.cls, eco=bundle.failure.eco)
        if bundle.failure.family_id != expected_family:
            raise ValueError("failure family_id does not match failure fields")
        if bundle.remedy.status != "proposed":
            raise ValueError("remote remedies must enter as proposed")
        validate_proof(bundle.proof)
        inspect_claim(
            err=bundle.failure.err,
            fix_k=bundle.remedy.fix.k,
            fix_b=bundle.remedy.fix.b,
            eval_cmd=bundle.proof.legacy_cmd or "true",
            note="",
            own=bundle.remedy.own,
            src="home",
        )
        remedy_payload = bundle.remedy.model_dump(mode="json")
        content_hash = remedy_payload.get("content_hash") or ""
        remedy_payload["content_hash"] = ""
        remedy_payload["signature"] = ""
        if content_hash != canonical_hash(remedy_payload):
            raise ValueError("remedy content_hash does not match remedy fields")

        signed = bool(bundle.remedy.signature or bundle.remedy.key_id)
        if signed:
            if bundle.remedy.own != bundle.remedy.key_id:
                raise ValueError("signed remedy owner must equal key_id")
            if not verify_record(bundle.remedy.model_dump(mode="json")):
                raise ValueError("invalid remedy signature")
        with self._conn() as con:
            for table, object_id, payload in (
                ("failures_v2", bundle.failure.id, bundle.failure.model_dump(mode="json")),
                ("proofs_v2", bundle.proof.id, bundle.proof.model_dump(mode="json")),
                ("remedies_v2", bundle.remedy.id, bundle.remedy.model_dump(mode="json")),
            ):
                row = con.execute(f"SELECT json FROM {table} WHERE id=?", (object_id,)).fetchone()
                if row and json.loads(row["json"]) != payload:
                    raise ValueError(f"{table} id collision with different content")
            con.execute(
                "INSERT OR IGNORE INTO failures_v2(id,fp_v1,family_id,cls,eco,json,created) VALUES(?,?,?,?,?,?,?)",
                (
                    bundle.failure.id,
                    bundle.failure.fp_v1,
                    bundle.failure.family_id,
                    bundle.failure.cls,
                    bundle.failure.eco,
                    bundle.failure.model_dump_json(),
                    bundle.failure.created.isoformat(),
                ),
            )
            con.execute(
                "INSERT OR IGNORE INTO proofs_v2(id,json,created) VALUES(?,?,?)",
                (bundle.proof.id, bundle.proof.model_dump_json(), bundle.proof.created.isoformat()),
            )
            con.execute(
                """INSERT OR IGNORE INTO remedies_v2
                   (id,failure_id,proof_id,legacy_claim_id,status,own,content_hash,json,created)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    bundle.remedy.id,
                    bundle.remedy.failure_id,
                    bundle.remedy.proof_id,
                    bundle.remedy.legacy_claim_id,
                    bundle.remedy.status,
                    bundle.remedy.own,
                    bundle.remedy.content_hash,
                    bundle.remedy.model_dump_json(),
                    bundle.remedy.created.isoformat(),
                ),
            )

    def add_relation(self, relation) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT OR IGNORE INTO relations_v2(id,source_id,target_id,kind,json,created) VALUES(?,?,?,?,?,?)",
                (relation.id, relation.source_id, relation.target_id, relation.kind, relation.model_dump_json(), relation.created.isoformat()),
            )

    def failure_graph(self, fp_v1: str) -> dict | None:
        with self._conn() as con:
            failure_row = con.execute("SELECT json,id FROM failures_v2 WHERE fp_v1=?", (fp_v1,)).fetchone()
            if not failure_row:
                return None
            remedies = [
                json.loads(row["json"])
                for row in con.execute(
                    "SELECT json FROM remedies_v2 WHERE failure_id=? ORDER BY created",
                    (failure_row["id"],),
                ).fetchall()
            ]
        return {"failure": json.loads(failure_row["json"]), "remedies": remedies}

    def protocol_events(self, *, after: int = 0, limit: int = 500) -> dict:
        import hashlib

        with self._conn() as con:
            rows = con.execute(
                "SELECT seq,json FROM protocol_events_v2 WHERE seq>? ORDER BY seq LIMIT ?",
                (max(0, int(after)), max(1, min(int(limit), 2000))),
            ).fetchall()
        events = [json.loads(row["json"]) for row in rows]
        cursor = int(rows[-1]["seq"]) if rows else max(0, int(after))
        material = "\n".join(json.dumps(event, sort_keys=True, separators=(",", ":")) for event in events)
        return {
            "v": 2,
            "after": max(0, int(after)),
            "next_cursor": cursor,
            "n": len(events),
            "batch_hash": hashlib.sha256(material.encode("utf-8")).hexdigest(),
            "events": events,
        }

    def import_protocol_events(self, events: list) -> dict[str, int]:
        from .graph import ProtocolEvent

        accepted = duplicate = 0
        with self._conn() as con:
            for raw in events[:2000]:
                event = raw if isinstance(raw, ProtocolEvent) else ProtocolEvent.model_validate(raw)
                cur = con.execute(
                    "INSERT OR IGNORE INTO protocol_events_v2(id,kind,object_id,actor,json,created) VALUES(?,?,?,?,?,?)",
                    (event.id, event.kind, event.object_id, event.actor, event.model_dump_json(), event.created.isoformat()),
                )
                if cur.rowcount:
                    accepted += 1
                else:
                    duplicate += 1
        return {"accepted": accepted, "duplicate": duplicate}

    def _record_claim_observation(
        self,
        claim: Claim,
        *,
        actor: str,
        held: bool,
        replayed: bool,
        detail: dict | None,
    ) -> None:
        import hashlib

        from .graph import Observation

        graph = self.graph(claim.id)
        if not graph:
            return
        remedy = graph["remedy"]
        proof = graph["proof"]
        blob = json.dumps(detail or {}, sort_keys=True, separators=(",", ":"), default=str)
        actual = (detail or {}).get("returncode")
        if not isinstance(actual, int):
            actual = (detail or {}).get("exit")
        env = (detail or {}).get("env") or {}
        trust_domain = (detail or {}).get("trust_domain") or ""
        sensor_plane = (detail or {}).get("sensor_plane") or ""
        observation = Observation(
            remedy_id=remedy["id"],
            proof_id=proof["id"],
            actor=actor,
            held=held,
            replayed=replayed,
            actual_exit=actual if isinstance(actual, int) else None,
            expected_exit=claim.eval.expect,
            environment={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            evidence_hash=hashlib.sha256(blob.encode("utf-8")).hexdigest() if detail else "",
            sandbox="legacy-allowlist" if replayed else "asserted",
            trust_domain=str(trust_domain),
            sensor_plane=str(sensor_plane),
        )
        self.add_observation(observation)

    @staticmethod
    def _graduate_home(claim: Claim) -> dict | None:
        """First local observation on a home row: drop remote hearsay counters.

        `src=home` means nc/nf/nr arrived from another trust domain. Flipping
        src to local without wiping them lets one local fail mint `confirmed`
        from remote nc, and one local confirm inherit an inflated score.
        SECURITY.md: remote hearsay does not become local proof. Keep the
        dropped counters in the returned audit dict for the event log.
        """
        if getattr(claim, "src", "local") != "home":
            return None
        audit = {
            "from_src": "home",
            "hearsay_nc": int(claim.nc or 0),
            "hearsay_nf": int(claim.nf or 0),
            "hearsay_nr": int(getattr(claim, "nr", 0) or 0),
            "hearsay_st": claim.st,
        }
        claim.src = "local"
        claim.nc = 0
        claim.nf = 0
        claim.nr = 0
        return audit

    @staticmethod
    def _merge_event_detail(detail: dict | None, hearsay: dict | None) -> dict | None:
        if not hearsay:
            return detail
        merged = dict(detail or {})
        merged["home_graduate"] = hearsay
        return merged

    def confirm(
        self,
        claim_id: str,
        actor: str = "did:claimidx:anon",
        *,
        replayed: bool = False,
        detail: dict | None = None,
    ) -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        # Graduate before counting so the local observation is the sole proof seed.
        hearsay = self._graduate_home(c)
        c.nc += 1
        if replayed:
            c.nr = int(getattr(c, "nr", 0) or 0) + 1
        c.refresh_status()
        self.put(c)
        event_detail = self._merge_event_detail(detail, hearsay)
        self._record_claim_observation(c, actor=actor, held=True, replayed=replayed, detail=event_detail)
        self._event(claim_id, "confirm-replay" if replayed else "confirm", actor, event_detail)
        from .session import session_id

        self.session_record(
            session_id(),
            kind="confirm-replay" if replayed else "confirm",
            fp=c.fp,
            claim_id=claim_id,
            detail={"replayed": bool(replayed), **({"home_graduate": hearsay} if hearsay else {})},
        )
        return c

    def reject(self, claim_id: str, actor: str = "did:claimidx:anon") -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        c.st = "rejected"
        self.put(c)
        self._event(claim_id, "reject", actor)
        return c

    def fail(
        self,
        claim_id: str,
        actor: str = "did:claimidx:anon",
        note: str = "",
        detail: dict | None = None,
        *,
        against: str | None = None,
    ) -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        # Graduate before counting so remote nc cannot mint confirmed on a local fail.
        hearsay = self._graduate_home(c)
        c.nf += 1
        if note:
            extra = f"fail: {note.strip()}"
            c.note = (c.note + (" | " if c.note else "") + extra)[:240]
        c.refresh_status()
        self.put(c)
        event_detail = self._merge_event_detail(detail, hearsay)
        self._record_claim_observation(c, actor=actor, held=False, replayed=bool(detail), detail=event_detail)
        self._event(claim_id, "fail", actor, event_detail)
        against_id = (against or "").strip()
        if against_id:
            from .graph import Relation

            src_graph = self.graph(claim_id)
            tgt_graph = self.graph(against_id)
            if not tgt_graph and len(against_id) == 64:
                # allow against a fingerprint: pick first remedy on that failure
                fg = self.failure_graph(against_id)
                if fg and fg.get("remedies"):
                    tgt_graph = {"remedy": fg["remedies"][0]}
            if src_graph and tgt_graph:
                self.add_relation(
                    Relation(
                        source_id=src_graph["remedy"]["id"],
                        target_id=tgt_graph["remedy"]["id"],
                        kind="contradicts",
                        actor=actor,
                    )
                )
        from .session import session_id

        self.session_record(
            session_id(),
            kind="fail",
            fp=c.fp,
            claim_id=claim_id,
            detail={"nf": c.nf, "against": against_id or None},
        )
        return c

    def alternatives(self, target: str) -> dict:
        """List remedies for a claim id or fp_v1. Additive browse over failure_graph."""
        target = (target or "").strip()
        if not target:
            return {"target": target, "remedies": [], "relations": []}
        claim = self.get(target)
        fp = claim.fp if claim else (target if len(target) == 64 else "")
        if not fp and claim is None:
            return {"target": target, "remedies": [], "relations": [], "error": "missing"}
        if not fp and claim is not None:
            fp = claim.fp
        graph = self.failure_graph(fp) if fp else None
        relations: list[dict] = []
        if not graph:
            one = self.graph(target) if claim else None
            if not one:
                return {"target": target, "fp": fp, "remedies": [], "relations": []}
            graph = {
                "failure": one.get("failure"),
                "remedies": [one["remedy"]] if one.get("remedy") else [],
            }
            relations = list(one.get("relations") or [])
        remedies = []
        remedy_ids: list[str] = []
        for rem in graph.get("remedies") or []:
            legacy = rem.get("legacy_claim_id") or ""
            row = self.get(legacy) if legacy else None
            from .match import annotate

            disp = annotate({"err": row.err, "fp": row.fp, "eco": row.eco, "rt": row.rt, "dep": row.dep}, row, 1.0).get("disposition") if row else None
            rid = rem.get("id") or ""
            if rid:
                remedy_ids.append(str(rid))
            remedies.append(
                {
                    "id": rid,
                    "legacy_claim_id": legacy,
                    "status": rem.get("status") or (row.st if row else ""),
                    "fix": rem.get("fix") or (row.fix.model_dump() if row else {}),
                    "nc": getattr(row, "nc", 0) if row else 0,
                    "nf": getattr(row, "nf", 0) if row else 0,
                    "nr": getattr(row, "nr", 0) if row else 0,
                    "disposition": disp,
                }
            )
        if remedy_ids and not relations:
            with self._conn() as con:
                placeholders = ",".join("?" * len(remedy_ids))
                rows = con.execute(
                    f"SELECT json FROM relations_v2 WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})",
                    (*remedy_ids, *remedy_ids),
                ).fetchall()
            relations = [json.loads(r["json"]) for r in rows]
        return {
            "target": target,
            "fp": fp,
            "remedies": remedies,
            "relations": relations,
        }

    def stats(self) -> dict:
        claims = self.all()
        by_st: dict[str, int] = {}
        for c in claims:
            by_st[c.st] = by_st.get(c.st, 0) + 1
        asks = ask_hits = ask_misses = 0
        ask_ms_sum = 0
        with self._conn() as con:
            rows = con.execute("SELECT kind, detail FROM events WHERE kind IN ('ask','hook')").fetchall()
        for r in rows:
            asks += 1
            raw = r["detail"] if "detail" in r.keys() else None
            blob: dict = {}
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        blob = parsed
                except json.JSONDecodeError:
                    blob = {}
            if blob.get("hit") is True:
                ask_hits += 1
            elif blob.get("hit") is False:
                ask_misses += 1
            ms = blob.get("ms")
            if isinstance(ms, int) and ms >= 0:
                ask_ms_sum += ms
        return {
            "n": len(claims),
            "status": by_st,
            "confirms": sum(c.nc for c in claims),
            "fails": sum(c.nf for c in claims),
            "asks": asks,
            "ask_hits": ask_hits,
            "ask_misses": ask_misses,
            "ask_ms_sum": ask_ms_sum,
            "actors": self.actor_n(),
        }

    def actor_n(self) -> int:
        with self._conn() as con:
            n = con.execute("SELECT COUNT(DISTINCT actor) FROM events WHERE actor IS NOT NULL AND actor != ''").fetchone()[0]
        return int(n or 0)

    def event_activity(self) -> dict[str, dict]:
        """All actors, all events. Not a recent window (that hides other providers)."""
        with self._conn() as con:
            rows = con.execute(
                "SELECT actor, kind, COUNT(*) AS c, MAX(ts) AS last FROM events WHERE actor IS NOT NULL AND actor != '' GROUP BY actor, kind"
            ).fetchall()
        by: dict[str, dict] = {}
        for r in rows:
            actor = r["actor"] or "did:claimidx:anon"
            slot = by.setdefault(
                actor,
                {"did": actor, "publish": 0, "confirm": 0, "fail": 0, "ask": 0, "share": 0, "last": r["last"]},
            )
            kind = r["kind"] or ""
            n = int(r["c"] or 0)
            if kind in ("home-push", "home-propose", "share"):
                slot["share"] += n
            elif kind in slot:
                slot[kind] += n
            elif kind == "hook":
                slot["ask"] += n
            last = r["last"]
            if last and (not slot.get("last") or str(last) > str(slot["last"])):
                slot["last"] = last
        return by

    def log_ask(
        self,
        actor: str,
        hits,
        *,
        ms: int | None = None,
        q: dict | None = None,
        kind: str = "ask",
    ) -> None:
        """Record ask/hook retrieve: hit, n, ms. Never stores the raw err."""
        n = len(hits or [])
        cid = hits[0][0].id if hits else ""
        detail: dict = {"hit": bool(hits), "n": n}
        if ms is not None:
            detail["ms"] = max(0, int(ms))
        if q:
            for key in ("fp", "eco", "cls"):
                val = q.get(key)
                if val:
                    detail[key] = val
        self.log(kind, actor, cid, detail=detail)
        from .session import session_id

        self.session_record(
            session_id(),
            kind=kind,
            fp=str((q or {}).get("fp") or "") or None,
            claim_id=cid or None,
            detail=detail,
        )

    def session_record(
        self,
        sid: str,
        *,
        kind: str,
        fp: str | None = None,
        claim_id: str | None = None,
        detail: dict | None = None,
    ) -> None:
        """Append a local session event. Never stores secrets or raw stderr blobs."""
        from .session import utc_ts

        sid = (sid or "").strip()
        if not sid:
            return
        blob = json.dumps(detail or {}, ensure_ascii=False, default=str)[:2000]
        with self._conn() as con:
            con.execute(
                "INSERT INTO session_events(session_id, ts, kind, fp, claim_id, detail) VALUES (?,?,?,?,?,?)",
                (sid[:120], utc_ts(), (kind or "")[:40], (fp or "")[:80] or None, (claim_id or "")[:64] or None, blob),
            )

    def session_summary(self, sid: str | None = None, *, fp: str | None = None) -> dict:
        """Compact view for agents: asks, ingests, fails, must_ask soft gate."""
        from .session import session_id as current_session

        sid = (sid or current_session()).strip()
        with self._conn() as con:
            rows = con.execute(
                "SELECT kind, fp, claim_id, detail, ts FROM session_events WHERE session_id=? ORDER BY id ASC",
                (sid,),
            ).fetchall()
        asks = 0
        asks_by_fp: dict[str, int] = {}
        fails_by_fp: dict[str, int] = {}
        ingests: list[dict] = []
        drafts: list[dict] = []
        last_disposition = None
        for r in rows:
            kind = r["kind"] or ""
            row_fp = r["fp"] or ""
            if kind in ("ask", "hook"):
                asks += 1
                if row_fp:
                    asks_by_fp[row_fp] = asks_by_fp.get(row_fp, 0) + 1
                try:
                    detail = json.loads(r["detail"] or "{}")
                except json.JSONDecodeError:
                    detail = {}
                if isinstance(detail, dict) and detail.get("disposition"):
                    last_disposition = detail.get("disposition")
            elif kind == "ingest":
                ingests.append({"claim_id": r["claim_id"], "fp": row_fp, "ts": r["ts"]})
            elif kind == "draft":
                drafts.append({"draft_id": r["claim_id"], "fp": row_fp, "ts": r["ts"]})
            elif kind == "fail" and row_fp:
                fails_by_fp[row_fp] = fails_by_fp.get(row_fp, 0) + 1
        focus = (fp or "").strip()
        fails_focus = fails_by_fp.get(focus, 0) if focus else (max(fails_by_fp.values()) if fails_by_fp else 0)
        asks_focus = asks_by_fp.get(focus, 0) if focus else 0
        # Soft gate: same fp failed twice this session — ask before a third try.
        must_ask = bool(focus and fails_focus >= 2) or (not focus and any(v >= 2 for v in fails_by_fp.values()))
        return {
            "session_id": sid,
            "asks": asks,
            "asks_by_fp": asks_by_fp,
            "fails_by_fp": fails_by_fp,
            "ingests": ingests[-10:],
            "drafts": drafts[-10:],
            "last_disposition": last_disposition,
            "must_ask": must_ask,
            "focus_fp": focus or None,
            "asks_focus": asks_focus,
            "fails_focus": fails_focus,
        }

    def log(self, kind: str, actor: str, claim_id: str = "", detail: dict | None = None) -> None:
        self._event(claim_id, kind, actor, detail)

    def log_force_reset(self, actor: str, claim_id: str, reset: dict) -> None:
        """Append the wiped hold. stdout/JSON force_reset is not the only record."""
        if force_reset_emits(reset):
            self.log("force_reset", actor, claim_id, detail=reset)

    def publish(self, claim: Claim, actor: str, reset: dict | None = None) -> Claim:
        """Replace the row and record the wipe in one sqlite transaction.

        A force_reset event without the new row would say the hold was
        wiped when it was not. Inspect refuses before any write.
        """
        reset = reset or {}
        self._prepare_write(claim)
        with self._conn() as con:
            if force_reset_emits(reset):
                self._insert_event(con, claim.id, "force_reset", actor, reset)
            self._upsert(con, claim)
            self._insert_event(con, claim.id, "publish", actor)
        from .session import session_id

        self.session_record(session_id(), kind="ingest", fp=claim.fp, claim_id=claim.id, detail={"st": claim.st})
        return claim

    def has_event(self, claim_id: str, kinds: tuple[str, ...] = ()) -> bool:
        if not kinds:
            return False
        placeholders = ",".join("?" * len(kinds))
        with self._conn() as con:
            row = con.execute(
                f"SELECT 1 FROM events WHERE claim_id=? AND kind IN ({placeholders}) LIMIT 1",
                (claim_id, *kinds),
            ).fetchone()
        return row is not None

    def events(self, limit: int = 100, actor: str | None = None) -> list[dict]:
        q = "SELECT claim_id, kind, actor, ts, detail FROM events"
        args: list = []
        if actor:
            q += " WHERE actor=?"
            args.append(actor)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as con:
            rows = con.execute(q, args).fetchall()
        out: list[dict] = []
        for r in rows:
            row = {"claim_id": r["claim_id"], "kind": r["kind"], "actor": r["actor"], "ts": r["ts"]}
            raw = r["detail"] if "detail" in r.keys() else None
            if raw:
                try:
                    row["detail"] = json.loads(raw)
                except json.JSONDecodeError:
                    row["detail"] = raw
            out.append(row)
        return out

    def _event(self, claim_id: str, kind: str, actor: str, detail: dict | None = None) -> None:
        with self._conn() as con:
            self._insert_event(con, claim_id, kind, actor, detail)

    def export_jsonl(self, path: str | Path) -> int:
        path = Path(path)
        rows = self.all()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for c in rows:
                f.write(c.model_dump_json() + "\n")
        return len(rows)

    def import_jsonl(self, path: str | Path) -> int:
        n = 0
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                self.put(Claim.model_validate(json.loads(line)))
            except (json.JSONDecodeError, ValueError, ValidationError):
                continue
            n += 1
        return n
