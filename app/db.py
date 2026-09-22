"""SQLite store. One writer (the worker), WAL mode, busy timeout.

Design points (from the 2026-09-21 plan review):
  * observations keep the latest value; any change to an already-stored value is appended to
    observation_history, so revisions are never lost.
  * every raw upstream payload is archived under data/raw/<source>/ with a checksum (raw_snapshots).
  * schema_version + ordered migrations instead of ad-hoc ALTERs.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from .config import DB_PATH, RAW

_lock = threading.RLock()

MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS series (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  instrument_type TEXT NOT NULL,  -- future_continuous | future_contract | spot_aggregated | etf | index | macro | holdings | premium | derived
  unit TEXT NOT NULL,
  source TEXT NOT NULL,
  cadence TEXT NOT NULL,          -- intraday | daily | weekly | monthly | quarterly | annual
  notes TEXT
);
CREATE TABLE IF NOT EXISTS observations (
  series_id TEXT NOT NULL,
  ts TEXT NOT NULL,               -- ISO-8601 UTC for intraday; 'YYYY-MM-DD' for daily+ (observation period)
  value REAL,
  source_published_at TEXT,       -- when the source says it published this value (if known)
  first_seen_at TEXT NOT NULL,
  ingested_at TEXT NOT NULL,      -- last retrieval that wrote this row
  revision INTEGER NOT NULL DEFAULT 1,
  meta TEXT,
  PRIMARY KEY (series_id, ts)
);
CREATE INDEX IF NOT EXISTS ix_obs_series_ts ON observations(series_id, ts DESC);
CREATE TABLE IF NOT EXISTS observation_history (
  series_id TEXT NOT NULL, ts TEXT NOT NULL, revision INTEGER NOT NULL,
  value REAL, meta TEXT, ingested_at TEXT NOT NULL, superseded_at TEXT NOT NULL,
  PRIMARY KEY (series_id, ts, revision)
);
CREATE TABLE IF NOT EXISTS raw_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL, url TEXT, retrieved_at TEXT NOT NULL,
  sha256 TEXT NOT NULL, bytes INTEGER NOT NULL, path TEXT NOT NULL, parser_version TEXT, status TEXT
);
CREATE INDEX IF NOT EXISTS ix_raw_source ON raw_snapshots(source, retrieved_at DESC);
CREATE TABLE IF NOT EXISTS cot (
  report_type TEXT NOT NULL,      -- legacy_fut | legacy_futopt | disagg_fut | disagg_futopt
  contract_code TEXT NOT NULL,
  report_date TEXT NOT NULL,      -- positions as of this Tuesday
  market_name TEXT,
  data TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,     -- when we fetched it (release is normally Friday 15:30 ET)
  PRIMARY KEY (report_type, contract_code, report_date)
);
CREATE TABLE IF NOT EXISTS articles (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL, publisher TEXT, author TEXT,
  published_at TEXT, event_at TEXT, ingested_at TEXT NOT NULL, feed TEXT,
  kind TEXT, metals TEXT, topics TEXT, geo TEXT, summary TEXT, why_matters TEXT, excerpt TEXT,
  content_stored INTEGER DEFAULT 0, headline_only INTEGER DEFAULT 1,
  cluster_id TEXT, score REAL DEFAULT 0, title_hash TEXT
);
CREATE INDEX IF NOT EXISTS ix_articles_pub ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS ix_articles_cluster ON articles(cluster_id);
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL, scheduled_at TEXT NOT NULL,
  source_url TEXT, importance INTEGER DEFAULT 2, notes TEXT, origin TEXT,
  actual TEXT, prior TEXT, consensus TEXT
);
CREATE TABLE IF NOT EXISTS briefings (
  date TEXT NOT NULL, generated_at TEXT NOT NULL, cutoff_at TEXT NOT NULL, tz TEXT NOT NULL,
  inputs_hash TEXT NOT NULL, analytics_version TEXT NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (date, generated_at)
);
CREATE TABLE IF NOT EXISTS driver_state (
  driver_id TEXT NOT NULL, date TEXT NOT NULL, generated_at TEXT NOT NULL, body TEXT NOT NULL,
  PRIMARY KEY (driver_id, date)
);
CREATE TABLE IF NOT EXISTS fundamentals (
  metric TEXT NOT NULL, period TEXT NOT NULL, value REAL, unit TEXT, source TEXT, source_url TEXT,
  published_at TEXT, notes TEXT, PRIMARY KEY (metric, period)
);
CREATE TABLE IF NOT EXISTS comex_stocks (
  metal TEXT NOT NULL, report_date TEXT NOT NULL, depository TEXT NOT NULL,
  registered REAL, eligible REAL, total REAL, registered_chg REAL, eligible_chg REAL, total_chg REAL,
  source_file TEXT, sha256 TEXT, ingested_at TEXT NOT NULL,
  PRIMARY KEY (metal, report_date, depository)
);
CREATE TABLE IF NOT EXISTS job_runs (
  job TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, ok INTEGER, rows INTEGER, message TEXT,
  PRIMARY KEY (job, started_at)
);
"""),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA busy_timeout=30000")
    return c


_con: sqlite3.Connection | None = None


def con() -> sqlite3.Connection:
    global _con
    if _con is None:
        _con = connect()
        migrate(_con)
    return _con


def migrate(c: sqlite3.Connection) -> None:
    c.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    have = {r[0] for r in c.execute("SELECT version FROM schema_version")}
    for v, sql in MIGRATIONS:
        if v in have:
            continue
        c.executescript(sql)
        c.execute("INSERT INTO schema_version(version, applied_at) VALUES(?,?)", (v, utcnow()))
        c.commit()


@contextmanager
def tx():
    with _lock:
        c = con()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise


def q(sql: str, params: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in con().execute(sql, params).fetchall()]


def q1(sql: str, params: tuple | list = ()) -> dict | None:
    r = q(sql, params)
    return r[0] if r else None


# ---------- series / observations ----------

def ensure_series(id, name, instrument_type, unit, source, cadence, notes=None):
    with tx() as c:
        c.execute("""INSERT INTO series(id,name,instrument_type,unit,source,cadence,notes) VALUES(?,?,?,?,?,?,?)
                     ON CONFLICT(id) DO UPDATE SET name=excluded.name, instrument_type=excluded.instrument_type,
                     unit=excluded.unit, source=excluded.source, cadence=excluded.cadence, notes=excluded.notes""",
                  (id, name, instrument_type, unit, source, cadence, notes))


def upsert_observations(series_id: str, rows: list[tuple], source_published_at: str | None = None) -> int:
    """rows: (ts, value, meta_dict|None). New values get revision 1; a changed value bumps the revision and
    archives the old one in observation_history. Unchanged values only refresh ingested_at."""
    now = utcnow()
    written = 0
    with tx() as c:
        for ts, v, m in rows:
            mj = json.dumps(m, default=str) if m else None
            cur = c.execute("SELECT value, meta, revision, ingested_at FROM observations WHERE series_id=? AND ts=?",
                            (series_id, ts)).fetchone()
            if cur is None:
                c.execute("""INSERT INTO observations(series_id,ts,value,source_published_at,first_seen_at,ingested_at,revision,meta)
                             VALUES(?,?,?,?,?,?,1,?)""", (series_id, ts, v, source_published_at, now, now, mj))
                written += 1
            else:
                same = (cur["value"] == v) or (cur["value"] is None and v is None) or \
                       (cur["value"] is not None and v is not None and abs(cur["value"] - v) < 1e-9)
                if same and (cur["meta"] == mj or mj is None):
                    c.execute("UPDATE observations SET ingested_at=? WHERE series_id=? AND ts=?", (now, series_id, ts))
                elif same:
                    c.execute("UPDATE observations SET ingested_at=?, meta=? WHERE series_id=? AND ts=?", (now, mj, series_id, ts))
                else:
                    c.execute("""INSERT OR REPLACE INTO observation_history(series_id,ts,revision,value,meta,ingested_at,superseded_at)
                                 VALUES(?,?,?,?,?,?,?)""",
                              (series_id, ts, cur["revision"], cur["value"], cur["meta"], cur["ingested_at"], now))
                    c.execute("""UPDATE observations SET value=?, meta=?, ingested_at=?, revision=revision+1,
                                 source_published_at=COALESCE(?, source_published_at) WHERE series_id=? AND ts=?""",
                              (v, mj, now, source_published_at, series_id, ts))
                    written += 1
    return written


def get_series(series_id: str, since: str | None = None, until: str | None = None,
               limit: int | None = None, asc=True) -> list[dict]:
    sql = "SELECT ts, value, meta, ingested_at, revision FROM observations WHERE series_id=?"
    p: list = [series_id]
    if since:
        sql += " AND ts>=?"; p.append(since)
    if until:
        sql += " AND ts<=?"; p.append(until)
    sql += " ORDER BY ts " + ("ASC" if asc else "DESC")
    if limit:
        sql += " LIMIT ?"; p.append(limit)
    rows = q(sql, p)
    for r in rows:
        r["meta"] = json.loads(r["meta"]) if r["meta"] else None
    return rows


def latest(series_id: str) -> dict | None:
    r = get_series(series_id, limit=1, asc=False)
    return r[0] if r else None


def series_meta(series_id: str) -> dict | None:
    return q1("SELECT * FROM series WHERE id=?", (series_id,))


def all_series() -> list[dict]:
    return q("SELECT s.*, (SELECT MAX(ts) FROM observations o WHERE o.series_id=s.id) AS last_ts, "
             "(SELECT COUNT(*) FROM observations o WHERE o.series_id=s.id) AS n FROM series s ORDER BY id")


# ---------- raw snapshots ----------

def archive_raw(source: str, url: str | None, content: bytes, ext: str = "bin",
                parser_version: str = "1", status: str = "ok") -> dict:
    """Write the raw payload to data/raw/<source>/<utc-date>/<time>_<sha8>.<ext> and index it."""
    now = datetime.now(timezone.utc)
    sha = hashlib.sha256(content).hexdigest()
    d = RAW / source / now.strftime("%Y-%m-%d")
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{now.strftime('%H%M%S')}_{sha[:8]}.{ext}"
    # skip writing an identical payload already archived today (saves disk on 5-min polls)
    dup = q1("SELECT path FROM raw_snapshots WHERE source=? AND sha256=? ORDER BY retrieved_at DESC LIMIT 1", (source, sha))
    if dup and Path(dup["path"]).exists():
        path = Path(dup["path"])
    else:
        path.write_bytes(content)
    with tx() as c:
        c.execute("INSERT INTO raw_snapshots(source,url,retrieved_at,sha256,bytes,path,parser_version,status) VALUES(?,?,?,?,?,?,?,?)",
                  (source, url, now.isoformat(timespec="seconds"), sha, len(content), str(path), parser_version, status))
    return {"sha256": sha, "path": str(path), "retrieved_at": now.isoformat(timespec="seconds")}


# ---------- jobs ----------

def log_job(job: str, started_at: str, ok: bool, rows: int, message: str = ""):
    with tx() as c:
        c.execute("INSERT OR REPLACE INTO job_runs(job,started_at,finished_at,ok,rows,message) VALUES(?,?,?,?,?,?)",
                  (job, started_at, utcnow(), 1 if ok else 0, rows, (message or "")[:500]))


def job_status() -> list[dict]:
    return q("""SELECT j.* , (SELECT MAX(finished_at) FROM job_runs k WHERE k.job=j.job AND k.ok=1) AS last_ok_at
                FROM job_runs j JOIN (SELECT job, MAX(started_at) m FROM job_runs GROUP BY job) x
                ON j.job=x.job AND j.started_at=x.m ORDER BY j.job""")
