"""제작 파이프라인. 작업(job) 하나 = 건축물 하나의 기획부터 업로드까지.

단계: generate -> prepare -> capture -> edit -> upload -> done
각 단계 결과는 job.checkpoint_json 에 저장되어 중단 지점부터 이어서 작업할 수 있다.
"""
from __future__ import annotations

import json
import random
import threading
import time
from datetime import date
from pathlib import Path

from mcbuildai.config import Config
from mcbuildai.db import DB, now_iso

STAGES = ["generate", "prepare", "capture", "edit", "upload", "done"]
# 작업자 역할: 촬영(Minecraft 필요)과 편집/업로드는 서로 다른 프로세스에서 병렬로 돌릴 수 있다
STAGE_ROLE = {"generate": "capture", "prepare": "capture", "capture": "capture", "edit": "edit", "upload": "upload"}


def STRINGS_SPEED(cfg) -> str:
    from mcbuildai import i18n
    return i18n.STRINGS[i18n.lang(cfg)]["speed"]


class JobCancelled(RuntimeError):
    pass


def create_job(db: DB, plan_json: str | None = None, building_id: int | None = None, priority: int = 5,
               schedule_id: int | None = None, experiment: bool = False, scheduled_for: str | None = None) -> int:
    ck = {"plan": json.loads(plan_json) if plan_json else None, "schedule_id": schedule_id}
    stage = "generate" if building_id is None else "prepare"
    jid = db.execute(
        "INSERT INTO jobs(building_id, stage, status, priority, checkpoint_json, experiment, scheduled_for, "
        "created_at, updated_at) VALUES (?,?, 'queued', ?,?,?,?,?,?)",
        (building_id, stage, priority, json.dumps(ck, ensure_ascii=False), "AB" if experiment else None,
         scheduled_for, now_iso(), now_iso()))
    if schedule_id:
        db.execute("UPDATE schedule SET job_id=? WHERE id=?", (jid, schedule_id))
    db.log("INFO", "jobs", f"작업 {jid} 생성 (단계 {stage})", jid)
    return jid


class Pipeline:
    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db
        self._server = None

    # ---------------- 공용 ----------------
    @property
    def server(self):
        if self._server is None:
            from mcbuildai.builder.server import ServerManager
            self._server = ServerManager(self.cfg, self.db)
        return self._server

    def _check_cancel(self, jid: int) -> None:
        r = self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))
        if r and r["status"] == "cancelled":
            raise JobCancelled(f"작업 {jid} 취소됨")

    def _save(self, jid: int, ck: dict, **fields) -> None:
        self.db.update_job(jid, checkpoint=ck, **fields)

    def run_stage(self, jid: int) -> str:
        job = self.db.one("SELECT * FROM jobs WHERE id=?", (jid,))
        stage = job["stage"]
        ck = json.loads(job["checkpoint_json"] or "{}")
        fn = getattr(self, f"stage_{stage}")
        fn(jid, job, ck)
        nxt = STAGES[STAGES.index(stage) + 1]
        self._save(jid, ck, stage=nxt, progress=0)
        self.db.log("INFO", "jobs", f"작업 {jid}: {stage} 완료 -> {nxt}", jid)
        return nxt

    # ---------------- 1. 생성 ----------------
    def stage_generate(self, jid: int, job: dict, ck: dict) -> None:
        from mcbuildai.generator.service import create_building
        from mcbuildai.planner.planner import Plan, Planner
        if job["building_id"]:
            return
        if ck.get("plan"):
            plan = Plan.from_json(json.dumps(ck["plan"]))
        else:
            plan = Planner(self.cfg, self.db).plan_day(date.today(), random.Random())
            ck["plan"] = json.loads(plan.to_json())
        engine = self.cfg.get("generator.engine", "auto")
        bid = create_building(self.cfg, self.db, plan.dna, plan.name, plan.concept, engine=engine)
        self.db.update_job(jid, building_id=bid)
        if ck.get("schedule_id"):
            self.db.execute("UPDATE schedule SET building_id=? WHERE id=?", (bid, ck["schedule_id"]))

    # ---------------- 2. 월드 준비 ----------------
    def stage_prepare(self, jid: int, job: dict, ck: dict) -> None:
        from mcbuildai.builder.engine import BuildEngine
        from mcbuildai.builder.order import order_blocks
        from mcbuildai.builder.world import next_plot, prepare_plot
        from mcbuildai.generator.service import building_dir, load_model
        from mcbuildai.jobs.support import ensure_disk
        bid = job["building_id"]
        ensure_disk(self.cfg, self.db)
        self.server.ensure_running()
        model = load_model(self.cfg, self.db, bid)
        work = building_dir(self.cfg, self.db, bid)
        ck["work_dir"] = str(work)
        if "origin" not in ck:
            idx, origin = next_plot(self.cfg, self.db)
            ck["plot_index"], ck["origin"] = idx, list(origin)
            self._save(jid, ck)
        origin = tuple(ck["origin"])
        if not ck.get("backup_done"):
            self.server.backup(reason=f"job{jid}")
            ck["backup_done"] = True
        with self.server.rcon() as r:
            lo, hi = prepare_plot(r, origin, model)
            ck["area"] = [list(lo), list(hi)]
            terrain = model.filter(parts=["terrain"])
            if len(terrain):
                items, _ = order_blocks(terrain, "bottom_up")
                BuildEngine(r, origin, items, bps=5000, use_fill=True).run()
        ck["terrain_done"] = True
        self.db.execute("UPDATE buildings SET status='prepared' WHERE id=?", (bid,))

    # ---------------- 3. 건축 + 촬영 ----------------
    def stage_capture(self, jid: int, job: dict, ck: dict) -> None:
        from mcbuildai.builder.engine import BuildEngine
        from mcbuildai.builder.order import order_blocks
        from mcbuildai.camera import paths as CP
        from mcbuildai.camera.capture import ScreenRecorder
        from mcbuildai.camera.client import ClientManager
        from mcbuildai.camera.controller import CameraController
        from mcbuildai.environment import presets as EP
        from mcbuildai.generator.service import load_model
        from mcbuildai.planner.dna import BuildingDNA
        from mcbuildai.timelapse.styles import STYLES as TL, plan_build_duration

        bid = job["building_id"]
        b = self.db.one("SELECT * FROM buildings WHERE id=?", (bid,))
        dna = BuildingDNA.from_json(b["dna_json"])
        model = load_model(self.cfg, self.db, bid)
        building = model.filter(exclude_parts=["terrain"])
        origin = tuple(ck["origin"])
        lo, hi = (tuple(x) for x in ck["area"])
        work = Path(ck["work_dir"])
        cap = work / "capture"
        cap.mkdir(parents=True, exist_ok=True)
        segs: dict = ck.setdefault("segments", {})
        live = self.cfg.path_of("paths.data_dir") / "live.jpg"
        self.server.ensure_running()
        r_main = self.server.rcon()
        r_cam = self.server.rcon()
        cam = CameraController(r_cam, self.cfg.get("client.camera_player"),
                               mode=self.cfg.get("camera.mode", "display"))
        client = ClientManager(self.cfg)
        try:
            if not cam.player_online():
                if self.cfg.get("client.launch_command"):
                    client.launch()
                client.wait_until_online(cam.player_online, float(self.cfg.get("client.startup_wait_sec")) + 120)
                time.sleep(float(self.cfg.get("client.startup_wait_sec")) / 4)
                if self.cfg.get("camera.auto_hide_hud", True):
                    client.hide_hud()
            preset = EP.preset_for(dna)
            EP.apply(r_main, preset, lo, hi)
            (bx0, by0, bz0), (bx1, by1, bz1) = building.bounds()
            frame = CP.Frame.from_bounds(origin, (bx0, by0, bz0), (bx1, by1, bz1))
            hero = CP.hero(frame, angle_deg=40)
            cam.setup(hero(0, 0))
            time.sleep(2.0)
            rec = ScreenRecorder(self.cfg, live_preview=live)

            def shoot(name: str, path_fn, seconds: float) -> None:
                if segs.get(name):
                    return
                self._check_cancel(jid)
                cam.move(*path_fn(0, 0))
                time.sleep(0.6)
                out = cap / f"{name}.mp4"
                rec.start(out)
                cam.run(path_fn, seconds, label=name)
                rec.stop()
                segs[name] = str(out)
                self._save(jid, ck)

            # Before (같은 구도로 After 와 비교)
            shoot("before", hero, 4.0)

            # 건축 (녹화 + 추적 카메라)
            st = TL[dna.timelapse_style]
            items, groups = order_blocks(building, st.order, dna.seed)
            n = len(items)
            _, bps = plan_build_duration(n, st.id, float(self.cfg.get("build.blocks_per_second")),
                                         min_sec=float(self.cfg.get("build.min_build_sec", 60)),
                                         max_sec=float(self.cfg.get("build.max_build_sec", 1800)))
            start = int(ck.get("build_index", 0))
            build_parts = segs.setdefault("build", [])
            if start < n:
                build_path = CP.build_path(dna.camera_style if dna.camera_style != "aerial" else "tracking", frame,
                                           shake=dna.camera_style == "handheld", seed=dna.seed)
                progress = {"i": start}
                stop = threading.Event()

                def on_progress(i, total):
                    progress["i"] = i
                    ck["build_index"] = i
                    self._save(jid, ck, progress=round(i / max(1, total), 4))
                    if self.db.one("SELECT status FROM jobs WHERE id=?", (jid,))["status"] == "cancelled":
                        stop.set()

                def paused() -> bool:
                    return bool(self.db.kv_get("pause_build", False))

                out = cap / f"build_{len(build_parts):03d}.mp4"
                cam.move(*build_path(progress["i"] / n, progress["i"] / n))
                rec.start(out)
                cam_thread = threading.Thread(
                    target=cam.run, args=(build_path, 0), daemon=True,
                    kwargs={"progress": lambda: progress["i"] / max(1, n), "stop": stop, "label": "build"})
                cam_thread.start()
                engine = BuildEngine(r_main, origin, items, bps=bps, speed_profile=st.speed_profile, groups=groups,
                                     group_pause=0.8 if st.speed_profile == "burst" else 0.0, use_fill=st.use_fill,
                                     batch=int(self.cfg.get("build.max_commands_per_batch")),
                                     on_progress=on_progress, should_pause=paused, stop_event=stop)
                try:
                    end = engine.run(start)
                finally:
                    stop.set()
                    cam_thread.join(timeout=5)
                    time.sleep(0.5)
                    rec.stop()
                    build_parts.append(str(out))
                    ck["build_index"] = progress["i"]
                    self._save(jid, ck)
                self._check_cancel(jid)
                if end < n:
                    raise RuntimeError(f"건축이 {end}/{n} 에서 중단되었습니다")
                ck["build_index"] = n
                ck["build_stats"] = {"placed": engine.stats.placed, "commands": engine.stats.commands,
                                     "errors": engine.stats.errors, "bps": round(bps, 1)}
                self._save(jid, ck)
            # 배치 오류 감지 및 수정
            if self.cfg.get("build.verify") and not ck.get("verified"):
                v_engine = BuildEngine(r_main, origin, items, bps=bps)
                vs = v_engine.verify(sample=float(self.cfg.get("build.verify_sample", 0.25)), fix=True, seed=dna.seed)
                ck["verify"] = {"checked": vs.verified, "fixed": vs.fixed,
                                "examples": [list(map(str, x)) for x in vs.failed_checks[:20]]}
                ck["verified"] = True
                self._save(jid, ck)
                if vs.fixed:
                    self.db.log("WARN", "build", f"작업 {jid}: 잘못 놓인 블록 {vs.fixed}개 수정", jid)
            # 완성 장면
            with EP.ParticleLoop(r_main, preset, (frame.cx, frame.base_y, frame.cz), frame.radius, frame.height):
                if preset.id == "sunset" and not segs.get("reveal"):
                    t = threading.Thread(target=EP.day_night_transition, args=(r_main, 11500, 13500, 14.0), daemon=True)
                    t.start()
                shoot("reveal", CP.orbit(frame, 40, 1.0, 0.5), 14.0)
                EP.apply(r_main, preset, lo, hi)
                shoot("aerial", CP.aerial(frame), 7.0)
                if dna.interior and frame.height >= 6 and dna.btype not in ("ziggurat", "citadel"):
                    shoot("interior", CP.interior(frame), 6.0)
                shoot("after", hero, 4.0)
            # 멀티 환경 렌더링
            if self.cfg.get("camera.multi_env", True):
                for p in EP.multi_env_set(preset):
                    key = f"env_{p.id}"
                    if segs.get(key):
                        continue
                    EP.apply(r_main, p, lo, hi)
                    time.sleep(2.0)
                    with EP.ParticleLoop(r_main, p, (frame.cx, frame.base_y, frame.cz), frame.radius, frame.height):
                        shoot(key, CP.orbit(frame, 40, 0.08, 0.45), 3.0)
                EP.apply(r_main, preset, lo, hi)
            cam.save_keyframes(cap / "camera_keyframes.json")
            ck["preset"] = preset.id
            self.db.execute("UPDATE buildings SET status='built' WHERE id=?", (bid,))
        finally:
            try:
                cam.teardown()
            except Exception:
                pass
            r_main.close()
            r_cam.close()

    # ---------------- 4. 편집 ----------------
    def stage_edit(self, jid: int, job: dict, ck: dict) -> None:
        from mcbuildai import i18n
        from mcbuildai.audio.library import build_soundtrack, choose_track, generate_music, get_analysis
        from mcbuildai.editor import ff
        from mcbuildai.editor.dedupe import find_duplicates
        from mcbuildai.editor.edit import THUMB_STYLES, EditOptions, Editor, make_thumbnails
        from mcbuildai.environment.presets import PRESETS
        from mcbuildai.planner.dna import BuildingDNA
        from mcbuildai.timelapse.styles import STYLES as TL

        bid = job["building_id"]
        b = self.db.one("SELECT * FROM buildings WHERE id=?", (bid,))
        dna = BuildingDNA.from_json(b["dna_json"])
        work = Path(ck["work_dir"])
        segs = ck["segments"]
        preset = PRESETS.get(ck.get("preset", "day_clear"))
        rng = random.Random(dna.seed)
        made = ck.setdefault("videos", {})
        if "raw" not in made:
            ed_raw = Editor(self.cfg, work)
            raw = ed_raw.render_raw(segs, work / "raw.mp4")
            made["raw"] = self._register_video(bid, jid, "raw", raw, {})
            self._save(jid, ck, progress=0.2)
        # 음악: 라이브러리에서 고르고, 부족하면 자동 수집, 그래도 없으면 AI 음악 API(설정 시)
        track = choose_track(self.cfg, self.db, dna.style, preset.grade, rng,
                             expected_reveal=0.72 * float(self.cfg.get('video.short_max_sec', 58)))
        if track is None:
            try:
                track = generate_music(self.cfg, f"{dna.style} {dna.btype} cinematic build timelapse", 60,
                                       self.cfg.path_of("paths.music_dir") / "generated")
                if track:
                    get_analysis(track, self.cfg)
            except Exception as e:
                self.db.log("WARN", "audio", f"AI 음악 생성 실패: {e}", jid)
            if track is None:
                self.db.log("WARN", "audio", "사용 가능한 음원이 없어 음악 없이 편집합니다", jid)
        bpm = (track.analysis or {}).get("bpm") if track else None
        V = self.cfg.get("video")
        blocks = int(b["block_count"] or 0)
        name = b["name"]
        style_n, type_n = i18n.style_name(self.cfg, dna.style), i18n.type_name(self.cfg, dna.btype)
        opts = EditOptions(
            grade=preset.grade, haze=preset.haze, edit_style=TL[dna.timelapse_style].edit,
            max_sec=float(V.get("short_max_sec", 58)),
            beat_period=(60.0 / bpm) if (bpm and V.get("beat_sync", True)) else None,
            hook=i18n.t(self.cfg, "hook", style=style_n, type=type_n) if V.get("hook_text", True) else "",
            hook_sub=f"{name}  |  " + i18n.t(self.cfg, "hook_sub", blocks=f"{blocks:,}") if V.get("hook_text", True) else name,
            complete_text=i18n.t(self.cfg, "complete"),
            cta=i18n.t(self.cfg, "cta") if V.get("cta_text", True) else "",
            cta_sub=i18n.t(self.cfg, "cta_sub") if V.get("cta_text", True) else "",
            before_label=i18n.t(self.cfg, "before"), after_label=i18n.t(self.cfg, "after"),
            blocks_label=i18n.t(self.cfg, "blocks"), speed_fmt=STRINGS_SPEED(self.cfg),
            block_count=blocks, show_counter=V.get("block_counter", True), show_speed=V.get("speed_label", True),
            motion_blur=V.get("motion_blur", True), loop_end=V.get("loop_end", True))

        def soundtrack(total, reveal_t, build_span, marks, out):
            return build_soundtrack(self.cfg, total, track, reveal_t, build_span, marks, out, rng)

        n_done = self.db.one("SELECT COUNT(*) c FROM videos WHERE variant='edited'")["c"]
        style_a = THUMB_STYLES[n_done % len(THUMB_STYLES)]
        variants = [("edited", opts, "vertical", style_a)]
        if job["experiment"] == "AB":
            variants.append(("edited_b", opts.variant_b(), "vertical",
                             THUMB_STYLES[(n_done + 1) % len(THUMB_STYLES)]))
        if V.get("landscape_edit", True):
            variants.append(("edited_landscape", opts.landscape(float(V.get("landscape_max_sec", 90))), "landscape",
                             style_a))
        thumbs = make_thumbnails(self.cfg, Path(segs.get("after") or segs["reveal"]),
                                 ff.duration(Path(segs.get("after") or segs["reveal"]), self.cfg) / 2,
                                 name, work, before_src=Path(segs["before"]) if segs.get("before") else None,
                                 style=style_a, subtitle=f"{style_n} {type_n}")
        ck["thumbnails"] = thumbs
        for vname, o, orient, tstyle in variants:
            if vname in made:
                continue
            ed = Editor(self.cfg, work, orientation=orient)
            out = work / f"{vname}.mp4"
            path, info = ed.render_edited(segs, name, o, out, soundtrack_fn=soundtrack)
            audio = info.get("audio") or {}
            info["music"] = audio.get("music")
            info["credits"] = audio.get("credits", [])
            info["thumbnail"] = thumbs.get(f"thumb_{tstyle}.jpg", thumbs["thumb.jpg"])
            info["thumbnail_style"] = tstyle
            info.pop("options", None)
            made[vname] = self._register_video(bid, jid, vname, path, info)
            if vname == "edited":
                ed.preview(out, work / "edited_preview.mp4")
            if self.cfg.get("video.cleanup_tmp", True):
                ed.cleanup()
            self._save(jid, ck, progress=min(0.95, 0.3 + 0.25 * len(made)))
        # 중복 영상 검사
        v = self.db.one("SELECT * FROM videos WHERE id=?", (made["edited"],))
        dups = find_duplicates(self.db, v["phash"], "edited", exclude_building=bid)
        ck["duplicates"] = [{"video_id": d["id"], "distance": d["distance"]} for d in dups]
        if dups:
            self.db.log("WARN", "editor", f"작업 {jid}: 기존 영상과 매우 비슷함 {ck['duplicates']}", jid)
        if self.cfg.get("video.cleanup_tmp", True):
            Editor(self.cfg, work).cleanup()
        self.db.execute("UPDATE buildings SET status='edited' WHERE id=?", (bid,))

    def _register_video(self, bid: int, jid: int, variant: str, path: Path, info: dict) -> int:
        from mcbuildai.editor import ff
        from mcbuildai.editor.dedupe import video_hash
        return self.db.execute(
            "INSERT INTO videos(building_id, job_id, variant, path, duration, phash, meta_json, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (bid, jid, variant, str(path), ff.duration(path, self.cfg), video_hash(path, self.cfg),
             json.dumps({**info, "variant": info.get("variant") or ("B" if variant == "edited_b" else "A")},
                        ensure_ascii=False), now_iso()))

    # ---------------- 5. 업로드 ----------------
    def stage_upload(self, jid: int, job: dict, ck: dict) -> None:
        from mcbuildai.planner.dna import BuildingDNA
        from mcbuildai.youtube.client import Uploader, next_publish_time
        from mcbuildai.youtube.metadata import generate_metadata
        from mcbuildai.jobs.support import notify
        bid = job["building_id"]
        b = self.db.one("SELECT * FROM buildings WHERE id=?", (bid,))
        dna = BuildingDNA.from_json(b["dna_json"])
        made = ck.get("videos", {})
        ups = ck.setdefault("uploads", {})
        targets = ["edited", "edited_b"] + (["edited_landscape"] if self.cfg.get("youtube.upload_landscape") else [])
        self.cfg.data.setdefault("_runtime", {})["trend_keywords"] = self.db.kv_get("trend_keywords", [])
        for variant in targets:
            if variant not in made or variant in ups:
                continue
            v = self.db.one("SELECT * FROM videos WHERE id=?", (made[variant],))
            meta = json.loads(v["meta_json"] or "{}")
            md = generate_metadata(self.cfg, b["name"], b["concept"] or "", dna, b["block_count"] or 0,
                                   meta.get("credits"), variant="long" if variant == "edited_landscape" else "short")
            publish_at = next_publish_time(self.cfg, self.db).isoformat()
            extra = {"language": md["language"], "localizations": md["localizations"],
                     "thumbnail": meta.get("thumbnail")}
            uid = self.db.execute(
                "INSERT INTO uploads(video_id, title, description, tags, privacy, publish_at, status, extra_json, "
                "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (v["id"], md["title"], md["description"], json.dumps(md["tags"], ensure_ascii=False),
                 self.cfg.get("youtube.privacy"), publish_at,
                 "pending" if not self.cfg.get("youtube.auto_upload") else "queued",
                 json.dumps(extra, ensure_ascii=False), now_iso(), now_iso()))
            ups[variant] = uid
            self._save(jid, ck)
        if self.cfg.get("youtube.auto_upload"):
            up = Uploader(self.cfg, self.db)
            for variant, uid in ups.items():
                row = self.db.one("SELECT status FROM uploads WHERE id=?", (uid,))
                if row["status"] in ("uploaded", "scheduled", "published"):
                    continue
                try:
                    up.upload(uid)
                except Exception as e:
                    self.db.execute("UPDATE uploads SET status='failed', error=?, updated_at=? WHERE id=?",
                                    (str(e)[:1000], now_iso(), uid))
                    raise
        self.db.execute("UPDATE buildings SET status='done' WHERE id=?", (bid,))
        notify(self.cfg, self.db, f"[mcbuildai] 제작 완료: {b['name']} (작업 {jid})")

    def stage_done(self, jid, job, ck):  # pragma: no cover
        return
