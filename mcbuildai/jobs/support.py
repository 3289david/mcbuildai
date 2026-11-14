"""알림, 저장 공간 관리, 오래된 작업 정리, 시스템 자원 조회."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcbuildai.config import Config
from mcbuildai.db import DB


def notify(cfg: Config, db: DB, text: str) -> None:
    """제작 완료/오류 알림 (Discord 웹훅 설정 시)."""
    url = cfg.get("notify.discord_webhook")
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=json.dumps({"content": text[:1900]}).encode(),
                                     headers={"Content-Type": "application/json", "User-Agent": "mcbuildai"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as e:
        db.log("WARN", "notify", f"알림 전송 실패: {e}")


def disk_free_gb(path: Path) -> float:
    path.mkdir(parents=True, exist_ok=True)
    return shutil.disk_usage(path).free / 1024 ** 3


def ensure_disk(cfg: Config, db: DB) -> None:
    """남은 공간이 기준보다 적으면 정리하고, 그래도 부족하면 예외."""
    out = cfg.path_of("paths.output_dir")
    need = float(cfg.get("jobs.min_free_disk_gb"))
    if disk_free_gb(out) >= need:
        return
    cleanup(cfg, db, aggressive=True)
    free = disk_free_gb(out)
    if free < need:
        raise RuntimeError(f"디스크 공간 부족: {free:.1f}GB 남음 (최소 {need}GB)")


def cleanup(cfg: Config, db: DB, aggressive: bool = False) -> dict:
    """오래된 작업 자동 정리: 완료된 작업의 원본 촬영 조각과 임시 파일을 지운다 (최종 영상은 보존)."""
    days = 3 if aggressive else int(cfg.get("jobs.cleanup_days"))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    removed, freed = 0, 0
    rows = db.query("SELECT id, checkpoint_json FROM jobs WHERE status IN ('done','cancelled') AND updated_at < ?",
                    (cutoff,))
    for r in rows:
        ck = json.loads(r["checkpoint_json"] or "{}")
        wd = ck.get("work_dir")
        if not wd:
            continue
        for sub in ("capture", "edit_tmp"):
            d = Path(wd) / sub
            if d.exists():
                freed += sum(p.stat().st_size for p in d.rglob("*") if p.is_file())
                shutil.rmtree(d, ignore_errors=True)
                removed += 1
    old_logs = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    db.execute("DELETE FROM logs WHERE ts < ?", (old_logs,))
    res = {"removed_dirs": removed, "freed_gb": round(freed / 1024 ** 3, 2)}
    if removed:
        db.log("INFO", "storage", f"정리: 폴더 {removed}개, {res['freed_gb']}GB 확보")
    return res


def system_stats(cfg: Config) -> dict:
    import psutil
    out = {"cpu_percent": psutil.cpu_percent(interval=0.3),
           "ram_percent": psutil.virtual_memory().percent,
           "ram_used_gb": round(psutil.virtual_memory().used / 1024 ** 3, 1),
           "ram_total_gb": round(psutil.virtual_memory().total / 1024 ** 3, 1),
           "disk_free_gb": round(disk_free_gb(cfg.path_of("paths.output_dir")), 1),
           "gpu": None}
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            p = subprocess.run([smi, "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5)
            name, util, used, total = [x.strip() for x in p.stdout.strip().splitlines()[0].split(",")]
            out["gpu"] = {"name": name, "util_percent": float(util), "mem_used_mb": float(used),
                          "mem_total_mb": float(total)}
        except Exception:
            out["gpu"] = None
    return out
