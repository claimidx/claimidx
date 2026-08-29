from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import Claim, utcnow


def _default_db() -> Path:
    return Path.home() / ".claimidx" / "index.sqlite"


DEFAULT_DB = _default_db()


class Store:
    def __init__(self, path: str | Path | None = None):
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
                    claim_id TEXT, kind TEXT, actor TEXT, ts TEXT
                )
                """
            )

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
        from datetime import datetime, timezone

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
                ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc) if ts_raw else utcnow()
            except (TypeError, ValueError, OSError):
                ts = utcnow()
            try:
                rebuilt.append(Claim(
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
                ))
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

    def put(self, claim: Claim) -> Claim:
        from .policy import inspect_claim, quarantine

        inspect_claim(
            err=claim.err, fix_k=claim.fix.k, fix_b=claim.fix.b,
            eval_cmd=claim.eval.cmd, note=claim.note, own=claim.own,
            src=getattr(claim, "src", "local") or "local",
        )
        quarantine(claim)
        claim.refresh_status()
        payload = claim.model_dump_json()
        with self._conn() as con:
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
        return claim

    def get(self, claim_id: str) -> Claim | None:
        with self._conn() as con:
            row = con.execute("SELECT json FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not row:
            return None
        claim = Claim.model_validate_json(row["json"])
        claim.refresh_status()
        return claim

    def by_fp(self, fp: str) -> list[Claim]:
        with self._conn() as con:
            rows = con.execute("SELECT json FROM claims WHERE fp=?", (fp,)).fetchall()
        return [Claim.model_validate_json(r["json"]) for r in rows]

    def all(self) -> list[Claim]:
        with self._conn() as con:
            rows = con.execute("SELECT json FROM claims").fetchall()
        claims = [Claim.model_validate_json(r["json"]) for r in rows]
        for c in claims:
            c.refresh_status()
        return claims

    def confirm(self, claim_id: str, actor: str = "did:claimidx:anon") -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        c.nc += 1
        if getattr(c, "src", "local") == "home":
            c.src = "local"
        c.refresh_status()
        self.put(c)
        self._event(claim_id, "confirm", actor)
        return c

    def reject(self, claim_id: str, actor: str = "did:claimidx:anon") -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        c.st = "rejected"
        self.put(c)
        self._event(claim_id, "reject", actor)
        return c

    def fail(self, claim_id: str, actor: str = "did:claimidx:anon") -> Claim:
        c = self.get(claim_id)
        if not c:
            raise KeyError(claim_id)
        c.nf += 1
        if getattr(c, "src", "local") == "home":
            c.src = "local"
        c.refresh_status()
        self.put(c)
        self._event(claim_id, "fail", actor)
        return c

    def stats(self) -> dict:
        claims = self.all()
        by_st: dict[str, int] = {}
        for c in claims:
            by_st[c.st] = by_st.get(c.st, 0) + 1
        return {"n": len(claims), "status": by_st, "confirms": sum(c.nc for c in claims), "fails": sum(c.nf for c in claims)}

    def log(self, kind: str, actor: str, claim_id: str = "") -> None:
        self._event(claim_id, kind, actor)

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
        q = "SELECT claim_id, kind, actor, ts FROM events"
        args: list = []
        if actor:
            q += " WHERE actor=?"
            args.append(actor)
        q += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self._conn() as con:
            rows = con.execute(q, args).fetchall()
        return [{"claim_id": r["claim_id"], "kind": r["kind"], "actor": r["actor"], "ts": r["ts"]} for r in rows]

    def _event(self, claim_id: str, kind: str, actor: str) -> None:
        with self._conn() as con:
            con.execute("INSERT INTO events(claim_id, kind, actor, ts) VALUES(?,?,?,?)", (claim_id, kind, actor, utcnow().isoformat()))

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
            except (json.JSONDecodeError, ValueError):
                continue
            n += 1
        return n
