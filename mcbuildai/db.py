"""SQLite 저장소. 건축물, 작업, 영상, 업로드, 성과, 로그를 기록한다."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    dna_json TEXT NOT NULL,
    dna_hash TEXT NOT NULL,
    btype TEXT NOT NULL,
    style TEXT NOT NULL,
    scale TEXT NOT NULL,
    palette TEXT NOT NULL,
    concept TEXT,
    timelapse_style TEXT,
    camera_style TEXT,
    difficulty REAL,
    est_minutes REAL,
    block_count INTEGER,
    schematic_path TEXT,
    preview_path TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    building_id INTEGER REFERENCES buildings(id),
    stage TEXT NOT NULL DEFAULT 'generate',
    status TEXT NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 5,
    attempts INTEGER NOT NULL DEFAULT 0,
    progress REAL NOT NULL DEFAULT 0,
    last_error TEXT,
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    worker TEXT,
    scheduled_for TEXT,
    experiment TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    building_id INTEGER REFERENCES buildings(id),
    job_id INTEGER REFERENCES jobs(id),
    variant TEXT NOT NULL,
    path TEXT NOT NULL,
    duration REAL,
    phash TEXT,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    youtube_id TEXT,
    url TEXT,
    title TEXT,
    description TEXT,
    tags TEXT,
    privacy TEXT,
    publish_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    extra_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER,
    avg_view_duration REAL, avg_view_percentage REAL
);
CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    job_id INTEGER
);
CREATE TABLE IF NOT EXISTS schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    building_id INTEGER,
    job_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts);
CREATE INDEX IF NOT EXISTS idx_buildings_hash ON buildings(dna_hash);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self.conn() as c:
            c.executescript(SCHEMA)
            # 이전 버전 DB 업그레이드
            cols = {r[1] for r in c.execute("PRAGMA table_info(uploads)").fetchall()}
            if "extra_json" not in cols:
                c.execute("ALTER TABLE uploads ADD COLUMN extra_json TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            try:
                yield c
                c.commit()
            finally:
                c.close()

    def execute(self, sql: str, params: tuple | dict = ()) -> int:
        with self.conn() as c:
            cur = c.execute(sql, params)
            return cur.lastrowid

    def query(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # --- 편의 함수 -------------------------------------------------------
    def log(self, level: str, source: str, message: str, job_id: int | None = None) -> None:
        self.execute(
            "INSERT INTO logs(ts, level, source, message, job_id) VALUES (?,?,?,?,?)",
            (now_iso(), level, source, message[:4000], job_id),
        )

    def kv_get(self, key: str, default: Any = None) -> Any:
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def update_job(self, job_id: int, **fields: Any) -> None:
        if "checkpoint" in fields:
            fields["checkpoint_json"] = json.dumps(fields.pop("checkpoint"), ensure_ascii=False)
        fields["updated_at"] = now_iso()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))

    def job_checkpoint(self, job_id: int) -> dict:
        r = self.one("SELECT checkpoint_json FROM jobs WHERE id=?", (job_id,))
        return json.loads(r["checkpoint_json"]) if r else {}
