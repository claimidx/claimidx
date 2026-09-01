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
            self._migrate_events_detail(con)

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
        c.nc += 1
        if replayed:
            c.nr = int(getattr(c, "nr", 0) or 0) + 1
        if getattr(c, "src", "local") == "home":
            c.src = "local"
        c.refresh_status()
        self.put(c)
        self._event(claim_id, "confirm-replay" if replayed else "confirm", actor, detail)
        return c

    def reject(self, claim_id: str, actor: str = "did:claimidx:anon") -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        c.st = "rejected"
        self.put(c)
        self._event(claim_id, "reject", actor)
        return c

    def fail(self, claim_id: str, actor: str = "did:claimidx:anon", note: str = "", detail: dict | None = None) -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        c.nf += 1
        if note:
            extra = f"fail: {note.strip()}"
            c.note = (c.note + (" | " if c.note else "") + extra)[:240]
        if getattr(c, "src", "local") == "home":
            c.src = "local"
        c.refresh_status()
        self.put(c)
        self._event(claim_id, "fail", actor, detail)
        return c

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
        }

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
