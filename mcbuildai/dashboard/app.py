"""관리자 대시보드 (FastAPI). 모든 수치는 DB, 파일 시스템, 운영체제에서 읽은 실제 값만 표시한다."""
from __future__ import annotations

import hmac
import hashlib
import json
import random
import shutil
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from mcbuildai.config import Config
from mcbuildai.db import DB, now_iso

STATIC = Path(__file__).parent / "static"
SECRET_KEYS = {("server", "rcon_password")}
SESSION_COOKIE = "mcb_session"
SESSION_MAX_AGE = 400 * 24 * 3600  # 400일 (브라우저가 허용하는 쿠키 수명 상한에 가깝게)


def create_app(cfg: Config, db: DB) -> FastAPI:
    app = FastAPI(title="mcbuildai 관리자")
    allowed_roots = [cfg.path_of("paths.output_dir").resolve(), cfg.path_of("paths.data_dir").resolve()]

    # ---------------- 로그인 (쿠키 세션, nginx Basic Auth 대신) ----------------
    # Basic Auth 는 브라우저/기기마다 다시 물어보는 경우가 많아서 매번 로그인해야 하는 게 불편했다.
    # 대신 로그인 한 번으로 400일짜리 서명된 쿠키를 심어서 그 뒤로는 다시 묻지 않는다.
    def _session_secret() -> str:
        s = cfg.get("dashboard.session_secret")
        if not s:
            raise HTTPException(500, "dashboard.session_secret 이 설정되지 않았습니다")
        return s

    def _sign(exp: int) -> str:
        mac = hmac.new(_session_secret().encode(), f"ok:{exp}".encode(), hashlib.sha256).hexdigest()
        return f"{exp}.{mac}"

    def _valid_session(token: str | None) -> bool:
        if not token or "." not in token:
            return False
        exp_s, _, mac = token.partition(".")
        try:
            exp = int(exp_s)
        except ValueError:
            return False
        if exp < time.time():
            return False
        expected = hmac.new(_session_secret().encode(), f"ok:{exp}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(mac, expected)

    LOGIN_EXEMPT_PREFIXES = ("/api/agent/", "/login")

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if path.startswith(LOGIN_EXEMPT_PREFIXES) or _valid_session(request.cookies.get(SESSION_COOKIE)):
            return await call_next(request)
        if path.startswith("/api/") or path == "/files":
            return JSONResponse({"detail": "로그인이 필요합니다"}, status_code=401)
        return RedirectResponse(f"/login?next={path}", status_code=302)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(error: str | None = None):
        msg = '<p style="color:#c33">비밀번호가 틀렸습니다.</p>' if error else ""
        return f"""<!doctype html><html><body style="font-family:sans-serif;max-width:360px;margin:80px auto">
        <h2>mcbuildai 로그인</h2>{msg}
        <form method="post" action="/login">
          <input name="password" type="password" placeholder="비밀번호" autofocus
                 style="width:100%;padding:8px;font-size:16px;box-sizing:border-box">
          <button style="width:100%;padding:8px;margin-top:8px;font-size:16px">로그인</button>
        </form></body></html>"""

    @app.post("/login")
    def login_submit(password: str = Form(...), next: str = "/"):
        if not hmac.compare_digest(password, str(cfg.get("dashboard.password", ""))):
            return RedirectResponse("/login?error=1", status_code=302)
        exp = int(time.time()) + SESSION_MAX_AGE
        resp = RedirectResponse(next or "/", status_code=302)
        resp.set_cookie(SESSION_COOKIE, _sign(exp), max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
        return resp

    @app.get("/logout")
    def logout():
        resp = RedirectResponse("/login", status_code=302)
        resp.delete_cookie(SESSION_COOKIE)
        return resp

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

    # ---------------- Windows 촬영 에이전트 ----------------
    # 주의: 경로가 /api/agent/ 로 시작하면 안 됨 — nginx 에서 그 접두사는 Windows 에이전트 자체 호출용으로
    # Basic Auth 를 뺐기 때문에, 토큰을 보여주는 이 라우트가 거기 걸리면 인증 없이 토큰이 노출된다.
    @app.get("/api/studio/agent_info")
    def agent_info():
        from mcbuildai.jobs.agent_hub import HUB
        exe_path = cfg.path_of("paths.data_dir") / "agent" / "mcbuildai-agent.exe"
        return {
            "enabled": bool(cfg.get("agent.enabled", True)),
            "token": cfg.get("agent.token"),
            "online": HUB.is_online(float(cfg.get("agent.heartbeat_timeout_sec", 25))),
            "exe_ready": exe_path.is_file(),
            "exe_download_url": f"/files?path={exe_path}" if exe_path.is_file() else None,
            "exe_built_at": datetime.fromtimestamp(exe_path.stat().st_mtime).isoformat()
                           if exe_path.is_file() else None,
            "seen_ips": {ip: datetime.fromtimestamp(ts).isoformat() for ip, ts in HUB.seen_ips.items()},
        }

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

    # ---------------- YouTube OAuth (웹 리다이렉트 흐름) ----------------
    # 헤드리스 서버라 InstalledAppFlow(로컬 브라우저 자동 실행)를 못 쓰므로,
    # 공개 redirect_uri로 돌아오는 표준 웹 OAuth 흐름을 쓴다.
    YOUTUBE_SCOPES = [
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/youtube.force-ssl",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ]

    def _oauth_redirect_uri() -> str:
        return cfg.get("dashboard.public_url", "").rstrip("/") + "/oauth2callback"

    # state -> PKCE code_verifier. authorization_url()이 매번 새 code_verifier를 만들기 때문에,
    # callback에서 토큰을 교환할 때 "같은" verifier를 다시 써야 한다 (안 그러면 invalid_grant).
    _oauth_pending: dict[str, str] = {}

    @app.get("/oauth2start")
    def oauth2_start():
        from google_auth_oauthlib.flow import Flow
        secrets = cfg.path_of("youtube.client_secrets")
        if not secrets.exists():
            raise HTTPException(400, f"OAuth 클라이언트 파일이 없습니다: {secrets}")
        flow = Flow.from_client_secrets_file(str(secrets), scopes=YOUTUBE_SCOPES, redirect_uri=_oauth_redirect_uri())
        auth_url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
        _oauth_pending[state] = flow.code_verifier
        return RedirectResponse(auth_url)

    @app.get("/oauth2callback", response_class=HTMLResponse)
    def oauth2_callback(request: Request):
        from google_auth_oauthlib.flow import Flow
        error = request.query_params.get("error")
        if error:
            return f"<h3>연결 실패</h3><p>{error}</p>"
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code:
            raise HTTPException(400, "code 파라미터가 없습니다")
        verifier = _oauth_pending.pop(state, None)
        secrets = cfg.path_of("youtube.client_secrets")
        flow = Flow.from_client_secrets_file(str(secrets), scopes=YOUTUBE_SCOPES, redirect_uri=_oauth_redirect_uri(),
                                             code_verifier=verifier)
        flow.fetch_token(code=code)
        creds = flow.credentials
        token = cfg.path_of("youtube.token_file")
        token.parent.mkdir(parents=True, exist_ok=True)
        token.write_text(creds.to_json(), encoding="utf-8")
        db.log("INFO", "youtube", "OAuth 연결 완료 (웹 흐름)")
        return "<h3>✅ YouTube 계정이 연결됐습니다. 이 탭은 닫으셔도 됩니다.</h3>"

    @app.get("/api/logs")
    def logs(level: str = "", limit: int = 300, source: str = ""):
        q, params = "SELECT * FROM logs WHERE 1=1", []
        if level:
            q += " AND level=?"; params.append(level)
        if source:
            q += " AND source=?"; params.append(source)
        q += " ORDER BY id DESC LIMIT ?"; params.append(limit)
        return db.query(q, tuple(params))

    # ---------------- 촬영용 에이전트(사용자 Windows PC) ----------------
    # 서버는 RCON 카메라 연출은 그대로 자기가 하고, 실제 마인크래프트 클라이언트 실행 +
    # 화면 녹화만 접속해 있는 에이전트에게 위임한다 (agent_hub.HUB 를 통한 폴링식 RPC).
    from mcbuildai.jobs.agent_hub import HUB

    def _check_agent_token(token: str):
        if not token or token != cfg.get("agent.token"):
            raise HTTPException(401, "잘못된 에이전트 토큰입니다")

    def _req_ip(request: Request) -> str:
        # Cloudflare 가 실제 접속자 IP를 이 헤더로 넣어준다 (nginx 가 X-Forwarded-For 를
        # /api/agent/ 경로엔 안 넘겨주므로 이걸로 봄). 여러 IP가 번갈아 보이면 Windows
        # 에이전트가 두 군데(PC) 이상에서 동시에 실행 중이라는 뜻.
        return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "?")

    @app.post("/api/agent/heartbeat")
    def agent_heartbeat(request: Request, token: str = Body(..., embed=True)):
        _check_agent_token(token)
        HUB.heartbeat(_req_ip(request))
        return {"ok": True}

    @app.get("/api/agent/poll")
    def agent_poll(request: Request, token: str):
        _check_agent_token(token)
        HUB.heartbeat(_req_ip(request))
        return {"commands": HUB.drain(_req_ip(request))}

    @app.post("/api/agent/ack")
    def agent_ack(request: Request, token: str = Body(...), id: str = Body(...), result: dict = Body({})):
        _check_agent_token(token)
        ip = _req_ip(request)
        HUB.ack(id, result, ip)
        db.log("INFO", "agent", f"에이전트 ack ({ip}): {id} -> {result}")
        return {"ok": True}

    @app.post("/api/agent/log")
    def agent_log(request: Request, token: str = Body(...), log: str = Body("")):
        _check_agent_token(token)
        HUB.set_console_log(log, _req_ip(request))
        return {"ok": True}

    @app.get("/api/studio/agent_log")
    def agent_log_view():
        return {"log": HUB.console_log, "updated_at": datetime.fromtimestamp(HUB.console_log_at).isoformat()
                if HUB.console_log_at else None}

    @app.post("/api/agent/upload")
    async def agent_upload(token: str = Form(...), name: str = Form(...), work_dir: str = Form(...),
                           file: UploadFile = File(...)):
        _check_agent_token(token)
        dest = Path(work_dir) / "capture" / f"{name}.mp4"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(await file.read())
        return {"ok": True, "path": str(dest)}

    @app.get("/api/agent/status")
    def agent_status():
        return {"online": HUB.is_online(float(cfg.get("agent.heartbeat_timeout_sec", 25)))}

    return app


def serve(cfg: Config, db: DB) -> None:
    import uvicorn
    app = create_app(cfg, db)
    uvicorn.run(app, host=cfg.get("dashboard.host"), port=int(cfg.get("dashboard.port")), log_level="warning")
