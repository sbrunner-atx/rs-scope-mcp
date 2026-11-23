"""Layer 4: the local data store.

Everything harvested from the scope lands here first; the assistant only sees what it
asks for. Layout under the data directory (default ~/rs-scope-data):

    catalog.sqlite                    sessions, captures, series, frames, measurements
    captures/<capture_id>/
        setup.set                     the scope's complete setup (SYSTem:SET?)
        CH1.npy, D0-D7.npy, ...       samples (numpy, memory-mappable)
        screen.png
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

import numpy as np

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, started REAL, goal TEXT, tags TEXT);
CREATE TABLE IF NOT EXISTS captures (
  id TEXT PRIMARY KEY, session_id TEXT, time REAL, kind TEXT, note TEXT, idn TEXT,
  settings TEXT, summary TEXT, segment INTEGER);
CREATE TABLE IF NOT EXISTS series (
  capture_id TEXT, source TEXT, kind TEXT, n INTEGER, t0 REAL, dt REAL, unit TEXT,
  stats TEXT, file TEXT, PRIMARY KEY (capture_id, source));
CREATE TABLE IF NOT EXISTS frames (
  capture_id TEXT, bus INTEGER, protocol TEXT, segment INTEGER, idx INTEGER, start REAL,
  stop REAL, status TEXT, address INTEGER, ident TEXT, data_hex TEXT, ascii TEXT, extra TEXT);
CREATE INDEX IF NOT EXISTS frames_cap ON frames (capture_id, bus, segment, idx);
CREATE TABLE IF NOT EXISTS measurements (
  capture_id TEXT, source TEXT, type TEXT, value REAL);
CREATE TABLE IF NOT EXISTS artifacts (
  capture_id TEXT, kind TEXT, file TEXT, meta TEXT);
"""


def _ascii(data: list) -> str:
    return "".join(chr(b) if isinstance(b, int) and 32 <= b < 127 else "." for b in data)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "catalog.sqlite"
        self.db = sqlite3.connect(self.db_path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._session: str | None = None

    # ------------------------------------------------------------------ sessions
    def start_session(self, goal: str = "", tags: list[str] | None = None) -> str:
        sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        self.db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid, time.time(), goal, json.dumps(tags or [])))
        self.db.commit()
        self._session = sid
        return sid

    def session(self) -> str:
        if self._session is None:
            row = self.db.execute("SELECT id FROM sessions ORDER BY started DESC LIMIT 1").fetchone()
            self._session = row[0] if row else self.start_session("default")
        return self._session

    def sessions(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT s.id, s.started, s.goal, COUNT(c.id) FROM sessions s LEFT JOIN captures c "
            "ON c.session_id = s.id GROUP BY s.id ORDER BY s.started DESC LIMIT ?", (limit,)).fetchall()
        return [{"id": r[0], "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(r[1])), "goal": r[2],
                 "captures": r[3]} for r in rows]

    # ------------------------------------------------------------------ captures
    def new_capture(self, kind: str, note: str | None = None, idn: str | None = None,
                    settings: dict | None = None, setup: bytes | None = None, segment: int | None = None) -> str:
        cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        d = self.dir(cid)
        if setup:
            (d / "setup.set").write_bytes(setup)
        self.db.execute("INSERT INTO captures VALUES (?,?,?,?,?,?,?,?,?)",
                        (cid, self.session(), time.time(), kind, note, idn, json.dumps(settings or {}), None, segment))
        self.db.commit()
        return cid

    def dir(self, cid: str) -> Path:
        if not re.fullmatch(r"[0-9A-Za-z-]+", cid):
            raise ValueError("bad capture id")
        d = self.root / "captures" / cid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def set_summary(self, cid: str, summary: dict) -> None:
        self.db.execute("UPDATE captures SET summary=? WHERE id=?", (json.dumps(summary, default=str), cid))
        self.db.commit()

    def add_series(self, cid: str, source: str, kind: str, t0: float, dt: float, unit: str,
                   values: np.ndarray, stats: dict) -> Path:
        safe = re.sub(r"[^0-9A-Za-z_.-]", "_", source)
        path = self.dir(cid) / f"{safe}.npy"
        np.save(path, values)
        self.db.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?,?,?,?,?,?)",
                        (cid, source, kind, int(values.shape[0]), t0, dt, unit, json.dumps(stats, default=str),
                         str(path.relative_to(self.root))))
        self.db.commit()
        return path

    def add_frames(self, cid: str, decoded: dict, segment: int | None = None) -> int:
        rows = []
        for f in decoded.get("frames", []):
            data = f.get("data") or f.get("mosi") or []
            extra = {k: v for k, v in f.items() if k not in ("index", "start", "stop", "status", "address", "id",
                                                              "data")}
            rows.append((cid, decoded.get("bus"), decoded.get("type"), segment, f.get("index"), f.get("start"),
                         f.get("stop"), f.get("status"), f.get("address"), f.get("id"),
                         " ".join(f"{b:02X}" for b in data if isinstance(b, int)), _ascii(data),
                         json.dumps(extra, default=str)))
        self.db.executemany("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.db.commit()
        return len(rows)

    def add_measurements(self, cid: str, source: str, values: dict) -> None:
        self.db.executemany("INSERT INTO measurements VALUES (?,?,?,?)",
                            [(cid, source, t, v) for t, v in values.items()])
        self.db.commit()

    def add_artifact(self, cid: str, kind: str, name: str, data: bytes, meta: dict | None = None) -> Path:
        path = self.dir(cid) / name
        path.write_bytes(data)
        self.db.execute("INSERT INTO artifacts VALUES (?,?,?,?)",
                        (cid, kind, str(path.relative_to(self.root)), json.dumps(meta or {}, default=str)))
        self.db.commit()
        return path

    # ------------------------------------------------------------------ reading
    def captures(self, limit: int = 20, session: str | None = None, kind: str | None = None) -> list[dict]:
        sql = "SELECT id, time, kind, note, summary, segment FROM captures"
        cond, args = [], []
        if session:
            cond.append("session_id = ?")
            args.append(session)
        if kind:
            cond.append("kind = ?")
            args.append(kind)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY time DESC LIMIT ?"
        args.append(limit)
        out = []
        for r in self.db.execute(sql, args).fetchall():
            srcs = [x[0] for x in self.db.execute("SELECT source FROM series WHERE capture_id=?", (r[0],))]
            nframes = self.db.execute("SELECT COUNT(*) FROM frames WHERE capture_id=?", (r[0],)).fetchone()[0]
            out.append({"id": r[0], "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[1])),
                        "kind": r[2], "note": r[3], "series": srcs, "frames": nframes,
                        **({"segment": r[5]} if r[5] is not None else {})})
        return out

    def capture(self, cid: str) -> dict | None:
        r = self.db.execute("SELECT id, time, kind, note, idn, settings, summary, segment FROM captures WHERE id=?",
                            (cid,)).fetchone()
        if not r:
            return None
        series = [{"source": s[0], "kind": s[1], "n": s[2], "t0": s[3], "dt": s[4], "unit": s[5],
                   "stats": json.loads(s[6] or "{}")}
                  for s in self.db.execute("SELECT source, kind, n, t0, dt, unit, stats FROM series "
                                           "WHERE capture_id=?", (cid,))]
        return {"id": r[0], "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[1])), "kind": r[2],
                "note": r[3], "idn": r[4], "settings": json.loads(r[5] or "{}"),
                "summary": json.loads(r[6] or "null"), "segment": r[7], "series": series,
                "files": str(self.dir(cid))}

    def series(self, cid: str, source: str) -> tuple[dict, np.ndarray]:
        r = self.db.execute("SELECT kind, n, t0, dt, unit, stats, file FROM series WHERE capture_id=? AND source=?",
                            (cid, source)).fetchone()
        if not r:
            raise KeyError(f"no series {source!r} in capture {cid}")
        arr = np.load(self.root / r[6], mmap_mode="r")
        return {"kind": r[0], "n": r[1], "t0": r[2], "dt": r[3], "unit": r[4], "stats": json.loads(r[5])}, arr

    def frames(self, cid: str | None = None, bus: int | None = None, status: str | None = None,
               address: int | None = None, contains: str | None = None, t_start: float | None = None,
               t_end: float | None = None, segment: int | None = None, offset: int = 0,
               limit: int = 50) -> dict:
        cond, args = [], []
        for col, val in (("capture_id", cid), ("bus", bus), ("address", address), ("segment", segment)):
            if val is not None:
                cond.append(f"{col} = ?")
                args.append(val)
        if status:
            cond.append("UPPER(status) " + ("!= 'OK'" if status.lower() == "not_ok" else "= UPPER(?)"))
            if status.lower() != "not_ok":
                args.append(status)
        if contains:
            cond.append("(ascii LIKE ? OR data_hex LIKE ?)")
            args += [f"%{contains}%", f"%{contains.upper()}%"]
        if t_start is not None:
            cond.append("start >= ?")
            args.append(t_start)
        if t_end is not None:
            cond.append("start <= ?")
            args.append(t_end)
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        total = self.db.execute(f"SELECT COUNT(*) FROM frames{where}", args).fetchone()[0]
        rows = self.db.execute(
            f"SELECT capture_id, bus, protocol, segment, idx, start, stop, status, address, ident, data_hex, ascii "
            f"FROM frames{where} ORDER BY capture_id, segment, idx LIMIT ? OFFSET ?",
            [*args, min(limit, 500), offset]).fetchall()
        cols = ["capture", "bus", "protocol", "segment", "index", "start_s", "stop_s", "status", "address",
                "id", "data_hex", "ascii"]
        return {"total": total, "offset": offset, "returned": len(rows), "columns": cols,
                "rows": [list(r) for r in rows]}

    def sql(self, query: str, limit: int = 200) -> dict:
        """Read-only SQL over the catalog (SELECT/WITH only, row-limited)."""
        q = query.strip().rstrip(";")
        if not re.match(r"(?is)^(select|with)\b", q) or ";" in q:
            raise ValueError("only a single SELECT or WITH statement is allowed")
        ro = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            cur = ro.execute(f"SELECT * FROM ({q}) LIMIT {int(min(limit, 1000))}")
            cols = [d[0] for d in cur.description]
            return {"columns": cols, "rows": [list(r) for r in cur.fetchall()]}
        finally:
            ro.close()
