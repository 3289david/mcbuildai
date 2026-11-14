"""관리자 대시보드 (FastAPI). 모든 수치는 DB, 파일 시스템, 운영체제에서 읽은 실제 값만 표시한다."""
from __future__ import annotations

import json
import random
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from mcbuildai.config import Config
from mcbuildai.db import DB, now_iso

STATIC = Path(__file__).parent / "static"
SECRET_KEYS = {("server", "rcon_password")}


def create_app(cfg: Config, db: DB) -> FastAPI:
    app = FastAPI(title="mcbuildai 관리자")
    allowed_roots = [cfg.path_of("paths.output_dir").resolve(), cfg.path_of("paths.data_dir").resolve()]

    def _bg(name: str, fn, *a, **kw):
        def run():
            try:
                fn(*a, **kw)
            except Exception as e:
                db.log("ERROR", "dashboard", f"{name} 실패: {e}")
        threading.Thread(target=run, daemon=True).start()

    # ---------------- 정적 ----------------
    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/static/{name}")
    def static(name: str):
        p = (STATIC / name).resolve()
        if p.parent != STATIC.resolve() or not p.exists():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/files")
    def files(path: str):
        p = Path(path).resolve()
        if not any(str(p).startswith(str(r)) for r in allowed_roots) or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p)

    @app.get("/live.jpg")
    def live():
        p = cfg.path_of("paths.data_dir") / "live.jpg"
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, headers={"Cache-Control": "no-store"})

    # ---------------- Dashboard ----------------
    @app.get("/api/overview")
    def overview():
        from mcbuildai.jobs.support import system_stats
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        today_iso = start.isoformat(timespec="seconds")
        today_jobs = db.query("SELECT id, stage, status, progress FROM jobs WHERE created_at >= ?", (today_iso,))
        stage_w = {"generate": 0.0, "prepare": 0.1, "capture": 0.2, "edit": 0.7, "upload": 0.9, "done": 1.0}
        if today_jobs:
            prog = sum(1.0 if j["status"] == "done" else stage_w.get(j["stage"], 0) +
                       0.1 * (j["progress"] or 0) for j in today_jobs) / len(today_jobs)
        else:
            prog = None
        running = db.one("SELECT j.*, b.name, b.preview_path FROM jobs j LEFT JOIN buildings b ON b.id=j.building_id "
                         "WHERE j.status='running' ORDER BY j.updated_at DESC LIMIT 1")
        live = cfg.path_of("paths.data_dir") / "live.jpg"
        live_age = time.time() - live.stat().st_mtime if live.exists() else None
        return {
            "today_progress": prog,
            "today_jobs": len(today_jobs),
            "today_uploads": db.one("SELECT COUNT(*) c FROM uploads WHERE youtube_id IS NOT NULL AND updated_at >= ?",
                                    (today_iso,))["c"],
            "queued": db.one("SELECT COUNT(*) c FROM jobs WHERE status IN ('queued','retry')")["c"],
            "waiting_buildings": db.query("SELECT j.id job_id, b.id, b.name, j.stage FROM jobs j LEFT JOIN buildings b "
                                          "ON b.id=j.building_id WHERE j.status IN ('queued','retry') "
                                          "ORDER BY j.priority, j.id LIMIT 10"),
            "current": running,
            "live_preview": {"available": live.exists(), "age_sec": round(live_age, 1) if live_age is not None else None},
            "render": db.query("SELECT j.id, j.progress, b.name FROM jobs j LEFT JOIN buildings b ON b.id=j.building_id "
                               "WHERE j.stage='edit' AND j.status='running'"),
            "system": system_stats(cfg),
            "recent_errors": db.query("SELECT ts, source, message FROM logs WHERE level='ERROR' ORDER BY id DESC LIMIT 8"),
            "youtube": db.query("SELECT u.id, u.title, u.status, u.publish_at, u.url FROM uploads u "
                                "ORDER BY u.id DESC LIMIT 8"),
            "build_paused": bool(db.kv_get("pause_build", False)),
        }

    # ---------------- AI Studio ----------------
    @app.get("/api/buildings")
    def buildings(limit: int = 100):
        return db.query("SELECT * FROM buildings ORDER BY id DESC LIMIT ?", (limit,))

    @app.get("/api/buildings/{bid}")
    def building(bid: int):
        b = db.one("SELECT * FROM buildings WHERE id=?", (bid,))
        if not b:
            raise HTTPException(404)
        d = Path(b["schematic_path"]).parent if b["schematic_path"] else None
        if d and (d / "materials.json").exists():
            b["materials"] = json.loads((d / "materials.json").read_text(encoding="utf-8"))
        if d and (d / "meta.json").exists():
            b["meta"] = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        b["files"] = sorted(str(p) for p in d.iterdir() if p.is_file()) if d and d.exists() else []
        return b

    @app.get("/api/catalog")
    def catalog():
        from mcbuildai.camera.paths import CAMERA_STYLES
        from mcbuildai.environment.presets import PRESETS
        from mcbuildai.generator.palette import PALETTES
        from mcbuildai.planner.dna import BUILDING_TYPES, SCALES, STYLES, TERRAINS
        from mcbuildai.timelapse.styles import STYLES as TL
        return {"types": BUILDING_TYPES, "styles": {k: v["ko"] for k, v in STYLES.items()},
                "scales": {k: v["ko"] for k, v in SCALES.items()}, "terrains": TERRAINS,
                "palettes": {k: v["ko"] for k, v in PALETTES.items()},
                "timelapse": {k: v.ko for k, v in TL.items()}, "camera": CAMERA_STYLES,
                "env_presets": {k: v.ko for k, v in PRESETS.items()}}

    @app.post("/api/studio/generate")
    async def studio_generate(mode: str = Form("random"), prompt: str = Form(""), engine: str = Form("auto"),
                              scale: str = Form(""), btype: str = Form(""), style: str = Form(""),
                              variant_of: str = Form(""), image: UploadFile | None = File(None)):
        from mcbuildai.generator.service import create_building
        from mcbuildai.planner.dna import BuildingDNA, mutate, random_dna
        from mcbuildai.planner.planner import Planner
        img_path = None
        if image is not None and image.filename:
            up = cfg.path_of("paths.data_dir") / "uploads"
            up.mkdir(parents=True, exist_ok=True)
            img_path = up / f"{int(time.time())}_{Path(image.filename).name}"
            img_path.write_bytes(await image.read())

        def job():
            pl = Planner(cfg, db)
            rng = random.Random()
            if mode == "variant" and variant_of:
                base = db.one("SELECT * FROM buildings WHERE id=?", (int(variant_of),))
                dna = mutate(BuildingDNA.from_json(base["dna_json"]), rng)
            elif mode in ("prompt", "image") and (prompt or img_path):
                dna = pl.dna_from_prompt(prompt or "첨부 이미지와 비슷한 건축물", [img_path] if img_path else None, rng)
            else:
                forced = {k: v for k, v in (("btype", btype), ("style", style), ("scale", scale)) if v}
                dna = random_dna(rng, pl.category_weights(), forced)
            if scale and dna.scale != scale:
                dna.scale = scale; dna.width = dna.depth = dna.floors = 0; dna.resolve_dimensions()
            name, concept, _ = pl.name_and_concept(dna, None, rng)
            create_building(cfg, db, dna, name, concept, engine=engine, user_prompt=prompt or None,
                            images=[img_path] if img_path else None,
                            base_building_id=int(variant_of) if variant_of else None)
        _bg("건축물 생성", job)
        return {"started": True}

    @app.post("/api/studio/genmc/pair")
    def genmc_pair(code: str = Body(..., embed=True)):
        from mcbuildai.generator.genmc import GenMC
        try:
            GenMC(cfg, db).pair(code)
        except Exception as e:
            raise HTTPException(400, str(e))
        return {"paired": True}

    @app.get("/api/studio/genmc/status")
    def genmc_status():
        from mcbuildai.generator.genmc import GenMC
        from mcbuildai.planner.ai_client import AIClient
        g = GenMC(cfg, db)
        out = {"paired": bool(g.resume_key), "ai_available": AIClient(cfg).available(), "status": None, "error": None}
        if g.resume_key:
            try:
                g.resume()
                out["status"] = g.status()[:2000]
            except Exception as e:
                out["error"] = str(e)
        return out

    # ---------------- Build Manager ----------------
    @app.get("/api/jobs")
    def jobs(limit: int = 100):
        rows = db.query("SELECT j.*, b.name, b.block_count FROM jobs j LEFT JOIN buildings b ON b.id=j.building_id "
                        "ORDER BY j.id DESC LIMIT ?", (limit,))
        for r in rows:
            ck = json.loads(r.pop("checkpoint_json") or "{}")
            r["build_index"] = ck.get("build_index")
            r["segments"] = list((ck.get("segments") or {}).keys())
            r["verify"] = ck.get("verify")
            r["build_stats"] = ck.get("build_stats")
        return rows

    @app.post("/api/jobs")
    def new_job(building_id: int | None = Body(None), priority: int = Body(5), experiment: bool = Body(False)):
        from mcbuildai.jobs.pipeline import create_job
        return {"job_id": create_job(db, building_id=building_id, priority=priority, experiment=experiment)}

    @app.post("/api/jobs/{jid}/action")
    def job_action(jid: int, action: str = Body(..., embed=True), value: int | None = Body(None, embed=True)):
        if action == "cancel":
            db.update_job(jid, status="cancelled")
        elif action == "retry":
            db.update_job(jid, status="queued", attempts=0, last_error=None)
        elif action == "priority" and value is not None:
            db.update_job(jid, priority=int(value))
        else:
            raise HTTPException(400, "알 수 없는 동작")
        db.log("INFO", "dashboard", f"작업 {jid}: {action}", jid)
        return {"ok": True}

    @app.post("/api/build/pause")
    def pause(paused: bool = Body(..., embed=True)):
        db.kv_set("pause_build", bool(paused))
        return {"paused": paused}

    @app.post("/api/server/{action}")
    def server_action(action: str):
        from mcbuildai.builder.server import ServerManager
        s = ServerManager(cfg, db)
        if action not in ("backup", "restart", "start"):
            raise HTTPException(400)
        _bg(f"서버 {action}", getattr(s, action))
        return {"started": True}

    # ---------------- Camera / Video / Music ----------------
    @app.get("/api/camera")
    def camera():
        from mcbuildai.environment.presets import available_resourcepacks, available_shaderpacks
        kf = []
        for r in db.query("SELECT checkpoint_json FROM jobs ORDER BY id DESC LIMIT 20"):
            ck = json.loads(r["checkpoint_json"] or "{}")
            p = Path(ck.get("work_dir", "")) / "capture" / "camera_keyframes.json"
            if ck.get("work_dir") and p.exists():
                kf.append(str(p))
        return {"settings": {"camera": cfg.get("camera", {}), "capture": cfg.get("capture"), "client": cfg.get("client")},
                "shaderpacks": available_shaderpacks(cfg), "resourcepacks": available_resourcepacks(cfg),
                "saved_paths": kf}

    @app.get("/api/video")
    def video():
        return {"settings": cfg.get("video"),
                "videos": db.query("SELECT v.id, v.variant, v.path, v.duration, v.created_at, b.name FROM videos v "
                                   "LEFT JOIN buildings b ON b.id=v.building_id ORDER BY v.id DESC LIMIT 60")}

    @app.get("/api/music")
    def music():
        from mcbuildai.audio.library import scan_library, sfx_files
        ok, rej = scan_library(cfg)
        return {"tracks": [{"file": t.path.name, "title": t.title, "artist": t.artist, "license": t.license,
                            "moods": t.moods, "source_url": t.source_url, "bpm": (t.analysis or {}).get("bpm"),
                            "duration": (t.analysis or {}).get("duration"), "path": str(t.path)} for t in ok],
                "fetch_report": db.kv_get("audio_fetch_report"),
                "rejected": [{"file": n, "reason": r} for n, r in rej],
                "sfx": {k: [p.name for p in sfx_files(cfg, k)] for k in ("place", "complete", "whoosh")},
                "music_dir": str(cfg.path_of("paths.music_dir")), "sfx_dir": str(cfg.path_of("paths.sfx_dir"))}

    @app.post("/api/music/fetch")
    def music_fetch(mood: str = Body("", embed=True)):
        from mcbuildai.audio.fetcher import AudioFetcher
        f = AudioFetcher(cfg, db)
        _bg("음원 자동 수집", (lambda: f.fetch_music(mood, 3)) if mood else f.ensure_library)
        return {"started": True}

    @app.get("/api/trends")
    def trends():
        return {"updated_at": db.kv_get("trend_updated_at"), "keywords": db.kv_get("trend_keywords", []),
                "weights": db.kv_get("trend_weights", {}), "videos": db.kv_get("trend_top_videos", [])}

    @app.post("/api/trends/update")
    def trends_update():
        from mcbuildai.youtube.trends import update_trends
        _bg("트렌드 분석", update_trends, cfg, db)
        return {"started": True}

    # ---------------- Upload Manager ----------------
    @app.get("/api/uploads")
    def uploads():
        return db.query("SELECT u.*, v.path, v.variant, b.name FROM uploads u JOIN videos v ON v.id=u.video_id "
                        "LEFT JOIN buildings b ON b.id=v.building_id ORDER BY u.id DESC LIMIT 100")

    @app.post("/api/uploads/{uid}/upload")
    def do_upload(uid: int):
        from mcbuildai.youtube.client import Uploader

        def run():
            try:
                Uploader(cfg, db).upload(uid)
            except Exception as e:
                db.execute("UPDATE uploads SET status='failed', error=?, updated_at=? WHERE id=?", (str(e)[:1000], now_iso(), uid))
                raise
        _bg("업로드", run)
        return {"started": True}

    @app.post("/api/uploads/{uid}/edit")
    def edit_upload(uid: int, title: str = Body(...), description: str = Body(...), publish_at: str | None = Body(None),
                    privacy: str = Body("private")):
        db.execute("UPDATE uploads SET title=?, description=?, publish_at=?, privacy=?, updated_at=? WHERE id=?",
                   (title, description, publish_at or None, privacy, now_iso(), uid))
        return {"ok": True}

    @app.post("/api/uploads/refresh")
    def refresh_uploads():
        from mcbuildai.youtube.client import Uploader
        _bg("업로드 상태 확인", lambda: Uploader(cfg, db).refresh_statuses())
        return {"started": True}

    @app.get("/api/youtube")
    def youtube_info():
        return {"connected": cfg.path_of("youtube.token_file").exists(), "channel": db.kv_get("youtube_channel"),
                "auto_upload": cfg.get("youtube.auto_upload"), "schedule_times": cfg.get("youtube.schedule_times")}

    # ---------------- Schedule / History / Analytics ----------------
    @app.get("/api/schedule")
    def schedule(days: int = 31):
        start = date.today() - timedelta(days=3)
        end = date.today() + timedelta(days=days)
        rows = db.query("SELECT s.*, j.status job_status, j.stage job_stage FROM schedule s LEFT JOIN jobs j "
                        "ON j.id=s.job_id WHERE date BETWEEN ? AND ? ORDER BY date, s.id",
                        (start.isoformat(), end.isoformat()))
        for r in rows:
            r["plan"] = json.loads(r.pop("plan_json"))
        return rows

    @app.post("/api/schedule/generate")
    def schedule_generate(days: int = Body(7, embed=True)):
        from mcbuildai.planner.planner import Planner
        ids = Planner(cfg, db).schedule(date.today(), days, int(cfg.get("jobs.daily_count")))
        return {"created": len(ids)}

    @app.post("/api/schedule/{sid}/start")
    def schedule_start(sid: int):
        from mcbuildai.jobs.pipeline import create_job
        s = db.one("SELECT * FROM schedule WHERE id=?", (sid,))
        if not s or s["job_id"]:
            raise HTTPException(400, "이미 작업이 있거나 없는 일정입니다")
        return {"job_id": create_job(db, s["plan_json"], schedule_id=sid, priority=3)}

    @app.get("/api/history")
    def history():
        rows = db.query("SELECT b.*, (SELECT COUNT(*) FROM videos v WHERE v.building_id=b.id) n_videos FROM buildings b "
                        "ORDER BY b.id DESC LIMIT 200")
        vids = db.query("SELECT id, building_id, variant, path, duration FROM videos")
        by_b: dict = {}
        for v in vids:
            by_b.setdefault(v["building_id"], []).append(v)
        for r in rows:
            r["videos"] = by_b.get(r["id"], [])
        return rows

    @app.get("/api/analytics")
    def analytics():
        from mcbuildai.analysis.performance import experiment_report, summarize
        return {"summary": summarize(db), "experiments": experiment_report(db),
                "latest": db.query("SELECT a.*, u.title FROM analytics a LEFT JOIN uploads u ON u.youtube_id=a.youtube_id "
                                   "WHERE a.id IN (SELECT MAX(id) FROM analytics GROUP BY youtube_id) "
                                   "ORDER BY a.views DESC LIMIT 50")}

    @app.post("/api/analytics/collect")
    def analytics_collect():
        from mcbuildai.youtube.client import collect_analytics
        _bg("성과 수집", collect_analytics, cfg, db)
        return {"started": True}

    # ---------------- Settings / Logs ----------------
    @app.get("/api/settings")
    def settings():
        data = json.loads(json.dumps(cfg.data))
        for sec, key in SECRET_KEYS:
            if data.get(sec, {}).get(key):
                data[sec][key] = "********"
        return data

    @app.put("/api/settings")
    def save_settings(data: dict = Body(...)):
        for sec, key in SECRET_KEYS:
            if data.get(sec, {}).get(key) == "********":
                data[sec][key] = cfg.get(f"{sec}.{key}")
        if not cfg.path:
            cfg.path = cfg.root / "config.yaml"
        cfg.data = data
        cfg.save()
        db.log("INFO", "dashboard", "설정 저장")
        return {"ok": True, "note": "일부 설정은 프로그램을 다시 시작해야 적용됩니다"}

    @app.get("/api/logs")
    def logs(level: str = "", limit: int = 300, source: str = ""):
        q, params = "SELECT * FROM logs WHERE 1=1", []
        if level:
            q += " AND level=?"; params.append(level)
        if source:
            q += " AND source=?"; params.append(source)
        q += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        return db.query(q, tuple(params))

    return app


def serve(cfg: Config, db: DB) -> None:
    import uvicorn
    app = create_app(cfg, db)
    uvicorn.run(app, host=cfg.get("dashboard.host"), port=int(cfg.get("dashboard.port")), log_level="warning")
