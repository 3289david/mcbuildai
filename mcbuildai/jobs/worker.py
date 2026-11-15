"""작업 대기열 처리, 자동 복구, 일일 스케줄러."""
from __future__ import annotations

import json
import os
import random
import socket
import threading
import time
import traceback
from datetime import date, datetime, timedelta

from mcbuildai.config import Config
from mcbuildai.db import DB, now_iso
from .pipeline import STAGE_ROLE, JobCancelled, Pipeline, create_job
from .support import cleanup, notify


def claim_job(db: DB, roles: list[str], worker: str, max_retries: int) -> dict | None:
    stages = [s for s, r in STAGE_ROLE.items() if r in roles]
    if not stages:
        return None
    q = ",".join("?" * len(stages))
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            f"SELECT * FROM jobs WHERE stage IN ({q}) AND (status='queued' OR (status='retry' AND attempts<?)) "
            f"AND (scheduled_for IS NULL OR scheduled_for <= ?) ORDER BY priority ASC, id ASC LIMIT 1",
            (*stages, max_retries, now_iso())).fetchone()
        if not row:
            return None
        c.execute("UPDATE jobs SET status='running', worker=?, updated_at=? WHERE id=?", (worker, now_iso(), row["id"]))
        return dict(row)


class Worker:
    def __init__(self, cfg: Config, db: DB, roles: list[str] | None = None, name: str | None = None):
        self.cfg = cfg
        self.db = db
        self.roles = roles or ["capture", "edit", "upload"]
        self.name = name or f"{socket.gethostname()}:{os.getpid()}:{'+'.join(self.roles)}"
        self.pipe = Pipeline(cfg, db)
        self.stop = threading.Event()
        self.max_retries = int(cfg.get("jobs.max_retries"))

    def recover_stale(self) -> None:
        """이전 실행에서 'running' 으로 남은 작업을 재시도 상태로 되돌린다 (중단 지점부터 이어서 작업).

        이 함수는 매 워커 스레드가 loop() 시작 시 '딱 한 번' 호출된다 - 그 시점에는 이 프로세스가
        아직 아무 작업도 claim 하지 않았으므로, 'running' 상태인 작업은 전부 이전 생애(프로세스
        재시작/크래시)에서 중단된 것이다. 예전엔 worker 이름이 hostname 으로 시작하는지, 그리고
        현재 self.name 과 다른지를 확인했는데, `mcbuildai run` 이 스레드를 'w0','w1'... 식 고정
        이름으로 매번 똑같이 재생성하기 때문에 재시작 후에도 worker 이름이 그대로라 절대 복구되지
        않는 버그가 있었다 (게다가 'w0' 는 hostname 으로 시작하지도 않아서 애초에 조건 자체가
        거의 항상 거짓이었다)."""
        rows = self.db.query("SELECT id FROM jobs WHERE status='running'")
        for r in rows:
            self.db.update_job(r["id"], status="retry", worker=None)
            self.db.log("WARN", "jobs", f"작업 {r['id']} 이전 실행 중단 감지, 이어서 진행 예정", r["id"])

    def handle(self, job: dict) -> None:
        jid = job["id"]
        while True:
            cur = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
            if cur["stage"] == "done":
                self.db.update_job(jid, status="done", progress=1.0, last_error=None)
                return
            if STAGE_ROLE.get(cur["stage"]) not in self.roles:
                # 다른 역할의 작업자에게 넘긴다
                self.db.update_job(jid, status="queued", worker=None)
                return
            if self.stop.is_set():
                self.db.update_job(jid, status="queued", worker=None)
                return
            self.db.update_job(jid, status="running")
            self.pipe.run_stage(jid)

    def run_one(self) -> bool:
        job = claim_job(self.db, self.roles, self.name, self.max_retries)
        if not job:
            return False
        jid = job["id"]
        try:
            self.handle(job)
        except JobCancelled:
            self.db.log("INFO", "jobs", f"작업 {jid} 취소", jid)
        except Exception as e:
            attempts = job["attempts"] + 1
            err = f"{type(e).__name__}: {e}"
            self.db.log("ERROR", "jobs", f"작업 {jid} 실패 ({attempts}/{self.max_retries}): {err}\n"
                                         f"{traceback.format_exc()[-1500:]}", jid)
            status = "retry" if attempts < self.max_retries else "failed"
            self.db.update_job(jid, status=status, attempts=attempts, last_error=err[:2000], worker=None)
            self._recover(e, job)
            if status == "failed":
                notify(self.cfg, self.db, f"[mcbuildai] 작업 {jid} 실패: {err[:500]}")
            else:
                time.sleep(min(300, 15 * 2 ** attempts))
        return True

    def _recover(self, e: Exception, job: dict) -> None:
        """Minecraft 충돌/RCON 끊김이면 서버를 재시작한다."""
        from mcbuildai.builder.rcon import RconError
        if isinstance(e, RconError) or "RCON" in str(e):
            try:
                self.pipe.server.ensure_running()
            except Exception as e2:
                self.db.log("ERROR", "server", f"서버 자동 재시작 실패: {e2}")

    def loop(self, idle_sleep: float = 10.0) -> None:
        self.recover_stale()
        self.db.log("INFO", "worker", f"작업자 시작: {self.name}")
        while not self.stop.is_set():
            if not self.run_one():
                self.stop.wait(idle_sleep)


class Scheduler:
    """매일 정해진 시각에 일정 보충 + 작업 생성, 주기적으로 성과/업로드 상태 수집과 정리."""

    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db
        self.stop = threading.Event()

    def tick(self, now: datetime | None = None) -> None:
        from zoneinfo import ZoneInfo
        from mcbuildai.planner.planner import Planner
        now = now or datetime.now(ZoneInfo(self.cfg.get("youtube.timezone", "UTC")))
        today = now.date().isoformat()
        hh, mm = (int(x) for x in self.cfg.get("jobs.daily_time", "09:00").split(":"))
        if self.db.kv_get("daily_done") != today and (now.hour, now.minute) >= (hh, mm):
            planner = Planner(self.cfg, self.db)
            planner.schedule(now.date(), int(self.cfg.get("planner.horizon_days", 7)),
                             int(self.cfg.get("jobs.daily_count")))
            exp_every = int(self.cfg.get("jobs.experiment_every", 5))
            for sid, plan in planner.plans_for(now.date()):
                n_jobs = len(self.db.query("SELECT id FROM jobs"))
                create_job(self.db, plan.to_json(), schedule_id=sid,
                           experiment=bool(exp_every) and n_jobs % exp_every == exp_every - 1)
            self.db.kv_set("daily_done", today)
        self._periodic("analytics_at", 6 * 3600, self._analytics)
        self._periodic("status_at", 1800, self._statuses)
        self._periodic("cleanup_at", 24 * 3600, lambda: cleanup(self.cfg, self.db))
        self._periodic("trends_at", 24 * 3600, self._trends)
        if self.cfg.get("audio.auto_fetch", True):
            self._periodic("audio_fetch_at", 7 * 24 * 3600, self._audio)

    def _periodic(self, key: str, every: int, fn) -> None:
        last = float(self.db.kv_get(key, 0))
        if time.time() - last < every:
            return
        self.db.kv_set(key, time.time())
        try:
            fn()
        except Exception as e:
            self.db.log("WARN", "scheduler", f"{key} 실패: {e}")

    def _analytics(self) -> None:
        from mcbuildai.youtube.client import collect_analytics
        if self.cfg.path_of("youtube.token_file").exists():
            collect_analytics(self.cfg, self.db)

    def _trends(self) -> None:
        import os
        from mcbuildai.youtube.trends import update_trends
        if os.environ.get(self.cfg.get("youtube.api_key_env", "YOUTUBE_API_KEY")) or \
                self.cfg.path_of("youtube.token_file").exists():
            update_trends(self.cfg, self.db)

    def _audio(self) -> None:
        from mcbuildai.audio.fetcher import AudioFetcher
        AudioFetcher(self.cfg, self.db).ensure_library()

    def _statuses(self) -> None:
        from mcbuildai.youtube.client import Uploader
        if self.cfg.path_of("youtube.token_file").exists():
            Uploader(self.cfg, self.db).refresh_statuses()

    def loop(self) -> None:
        self.db.log("INFO", "scheduler", "스케줄러 시작")
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception as e:
                self.db.log("ERROR", "scheduler", f"스케줄러 오류: {e}")
            self.stop.wait(30)
