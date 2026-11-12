"""SQLite storage: sources, immutable snapshots, mappings, runs, clusters, triaged issues."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

import pandas as pd

from .models import Issue

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY, kind TEXT, config TEXT, entities TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, entity TEXT, fetched_at TEXT, row_count INTEGER);
CREATE TABLE IF NOT EXISTS raw_rows(snapshot_id INTEGER, key TEXT, payload TEXT);
CREATE INDEX IF NOT EXISTS ix_raw ON raw_rows(snapshot_id);
CREATE TABLE IF NOT EXISTS mappings(source TEXT, source_entity TEXT, canonical_entity TEXT, columns TEXT, PRIMARY KEY(source, source_entity));
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS clusters(run_id INTEGER, entity TEXT, cluster_id TEXT, rid TEXT, confidence REAL, reason TEXT);
CREATE TABLE IF NOT EXISTS issues(fingerprint TEXT PRIMARY KEY, rule TEXT, severity TEXT, entity TEXT, source TEXT, record_key TEXT,
  field TEXT, value TEXT, expected TEXT, message TEXT, record_ids TEXT, details TEXT, status TEXT DEFAULT 'open', note TEXT DEFAULT '',
  first_run INTEGER, last_run INTEGER, first_seen TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""
SECRET_KEYS = {"token", "password", "api_key"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("DQP_DB", "data/dqp.db")
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # ---- sources -------------------------------------------------------
    def add_source(self, name: str, kind: str, config: dict[str, Any], entities: list[str] | None = None) -> None:
        safe = {k: v for k, v in config.items() if k not in SECRET_KEYS}  # secrets are never persisted
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?)",
                      (name, kind, json.dumps(safe), json.dumps(entities or []), now()))

    def sources(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM sources ORDER BY created_at, name").fetchall()
        return [{"name": r["name"], "kind": r["kind"], "config": json.loads(r["config"]),
                 "entities": json.loads(r["entities"] or "[]")} for r in rows]

    def delete_source(self, name: str) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM sources WHERE name=?", (name,))
            c.execute("DELETE FROM mappings WHERE source=?", (name,))

    # ---- snapshots ------------------------------------------------------
    def save_snapshot(self, source: str, entity: str, rows: Iterable[dict[str, Any]]) -> int:
        rows = list(rows)
        with self.conn() as c:
            cur = c.execute("INSERT INTO snapshots(source,entity,fetched_at,row_count) VALUES(?,?,?,?)",
                            (source, entity, now(), len(rows)))
            sid = cur.lastrowid
            c.executemany("INSERT INTO raw_rows VALUES(?,?,?)",
                          [(sid, str(r["_key"]), json.dumps(r, default=str)) for r in rows])
        return int(sid)

    def latest_snapshots(self) -> pd.DataFrame:
        with self.conn() as c:
            return pd.read_sql_query(
                "SELECT s.* FROM snapshots s JOIN (SELECT source, entity, MAX(id) mid FROM snapshots GROUP BY source, entity) m "
                "ON s.id = m.mid ORDER BY s.source, s.entity", c)

    def rows(self, source: str, entity: str) -> pd.DataFrame:
        with self.conn() as c:
            sid = c.execute("SELECT MAX(id) FROM snapshots WHERE source=? AND entity=?", (source, entity)).fetchone()[0]
            if sid is None:
                return pd.DataFrame()
            data = [json.loads(r[0]) for r in c.execute("SELECT payload FROM raw_rows WHERE snapshot_id=?", (sid,))]
        df = pd.DataFrame(data)
        if not df.empty:
            df = df[["_key"] + [c for c in df.columns if c != "_key"]]
        return df

    # ---- mappings --------------------------------------------------------
    def save_mapping(self, source: str, source_entity: str, canonical_entity: str, columns: dict[str, str]) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO mappings VALUES(?,?,?,?)",
                      (source, source_entity, canonical_entity, json.dumps(columns)))

    def mappings(self) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute("SELECT * FROM mappings ORDER BY source, source_entity").fetchall()
        return [{"source": r["source"], "source_entity": r["source_entity"],
                 "canonical_entity": r["canonical_entity"], "columns": json.loads(r["columns"])} for r in rows]

    def get_mapping(self, source: str, source_entity: str) -> dict[str, Any] | None:
        return next((m for m in self.mappings() if m["source"] == source and m["source_entity"] == source_entity), None)

    # ---- runs / clusters / issues -----------------------------------------
    def start_run(self) -> int:
        with self.conn() as c:
            return int(c.execute("INSERT INTO runs(started_at) VALUES(?)", (now(),)).lastrowid)

    def finish_run(self, run_id: int, summary: dict[str, Any]) -> None:
        with self.conn() as c:
            c.execute("UPDATE runs SET finished_at=?, summary=? WHERE id=?", (now(), json.dumps(summary, default=str), run_id))

    def latest_run(self) -> dict[str, Any] | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
        return None if not r else {"id": r["id"], "finished_at": r["finished_at"], "summary": json.loads(r["summary"] or "{}")}

    def runs(self, limit: int = 20) -> pd.DataFrame:
        with self.conn() as c:
            return pd.read_sql_query("SELECT id, started_at, finished_at FROM runs ORDER BY id DESC LIMIT ?", c, params=(limit,))

    def replace_clusters(self, run_id: int, rows: list[tuple]) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM clusters")
            c.executemany("INSERT INTO clusters VALUES(?,?,?,?,?,?)", [(run_id, *r) for r in rows])

    def clusters(self) -> pd.DataFrame:
        with self.conn() as c:
            return pd.read_sql_query("SELECT entity, cluster_id, rid, confidence, reason FROM clusters", c)

    def upsert_issues(self, run_id: int, issues: list[Issue]) -> None:
        """Keep triage status across runs. Issues that vanished are auto-closed as 'fixed'."""
        ts = now()
        with self.conn() as c:
            for i in issues:
                r = i.to_row()
                c.execute(
                    "INSERT INTO issues(fingerprint,rule,severity,entity,source,record_key,field,value,expected,message,record_ids,details,"
                    "status,first_run,last_run,first_seen,last_seen) VALUES(:fingerprint,:rule,:severity,:entity,:source,:record_key,:field,"
                    ":value,:expected,:message,:record_ids,:details,'open',:run,:run,:ts,:ts) "
                    "ON CONFLICT(fingerprint) DO UPDATE SET severity=:severity, expected=:expected, message=:message, record_ids=:record_ids,"
                    "details=:details, last_run=:run, last_seen=:ts, "
                    "status=CASE WHEN status='fixed' THEN 'open' ELSE status END",
                    {**r, "run": run_id, "ts": ts})
            c.execute("UPDATE issues SET status='fixed', note=CASE WHEN note='' THEN 'Auto-closed: no longer found' ELSE note END "
                      "WHERE last_run<? AND status='open'", (run_id,))

    def issues(self) -> pd.DataFrame:
        with self.conn() as c:
            return pd.read_sql_query("SELECT * FROM issues ORDER BY CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, rule", c)

    def set_status(self, fingerprints: list[str], status: str, note: str = "") -> None:
        with self.conn() as c:
            c.executemany("UPDATE issues SET status=?, note=? WHERE fingerprint=?", [(status, note, f) for f in fingerprints])

    # ---- settings ----------------------------------------------------------
    def get_setting(self, key: str, default: str = "") -> str:
        with self.conn() as c:
            r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, value))

    def data_version(self) -> str:
        with self.conn() as c:
            a = c.execute("SELECT COALESCE(MAX(id),0) FROM snapshots").fetchone()[0]
            b = c.execute("SELECT COALESCE(MAX(id),0) FROM runs WHERE finished_at IS NOT NULL").fetchone()[0]
            m = c.execute("SELECT COUNT(*) FROM mappings").fetchone()[0]
            s = c.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        return f"{a}-{b}-{m}-{s}"
