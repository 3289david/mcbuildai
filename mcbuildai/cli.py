"""명령줄 진입점: python -m mcbuildai <명령>"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
from datetime import date
from pathlib import Path

import yaml

from .config import DEFAULTS, load_config
from .db import DB


def _ctx(args):
    cfg = load_config(args.config)
    db = DB(cfg.path_of("paths.data_dir") / "mcbuildai.db")
    return cfg, db


def cmd_init(args):
    p = Path(args.config)
    if p.exists():
        print(f"이미 있습니다: {p}")
    else:
        p.write_text(yaml.safe_dump(DEFAULTS, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"설정 파일 생성: {p}")
    cfg, db = _ctx(args)
    for k in ("paths.music_dir", "paths.sfx_dir", "server.dir", "server.backup_dir"):
        cfg.path_of(k).mkdir(parents=True, exist_ok=True)
    print("폴더 준비 완료. config.yaml 에서 server.rcon_password, client.camera_player 등을 설정하세요.")


def cmd_plan(args):
    from .planner.planner import Planner
    cfg, db = _ctx(args)
    start = date.fromisoformat(args.start) if args.start else date.today()
    ids = Planner(cfg, db).schedule(start, args.days, args.per_day, seed=args.seed)
    for r in db.query(f"SELECT date, plan_json FROM schedule WHERE id IN ({','.join('?' * len(ids))})", ids) if ids else []:
        p = json.loads(r["plan_json"])
        d = p["dna"]
        print(f"{r['date']}  {p['name']}  [{d['btype']}/{d['style']}/{d['scale']}/{d['terrain']}] "
              f"연출={d['timelapse_style']} 카메라={d['camera_style']}" + (f" 이벤트={p['event']}" if p.get("event") else ""))


def cmd_generate(args):
    from .generator.service import create_building
    from .planner.dna import BuildingDNA, mutate
    from .planner.planner import Planner
    cfg, db = _ctx(args)
    pl = Planner(cfg, db)
    rng = random.Random(args.seed)
    images = [Path(args.image)] if args.image else None
    if args.variant_of:
        base = db.one("SELECT * FROM buildings WHERE id=?", (args.variant_of,))
        dna = mutate(BuildingDNA.from_json(base["dna_json"]), rng)
        name, concept, _ = pl.name_and_concept(dna, None, rng)
    elif args.prompt or images:
        dna = pl.dna_from_prompt(args.prompt or "첨부 이미지와 비슷한 건축물", images, rng)
        name, concept, _ = pl.name_and_concept(dna, None, rng)
    else:
        plan = pl.plan_day(date.today(), rng)
        dna, name, concept = plan.dna, plan.name, plan.concept
    if args.scale:
        dna.scale = args.scale; dna.width = dna.depth = dna.floors = 0; dna.resolve_dimensions()
    bid = create_building(cfg, db, dna, name, concept, engine=args.engine, user_prompt=args.prompt,
                          images=images, base_building_id=args.variant_of)
    b = db.one("SELECT * FROM buildings WHERE id=?", (bid,))
    print(json.dumps({k: b[k] for k in ("id", "name", "btype", "style", "scale", "block_count", "difficulty",
                                         "est_minutes", "schematic_path", "preview_path")}, ensure_ascii=False, indent=1))


def cmd_genmc(args):
    from .generator.genmc import GenMC
    cfg, db = _ctx(args)
    g = GenMC(cfg, db)
    if args.action == "pair":
        g.pair(args.code)
        print("페어링 완료")
    else:
        g.resume()
        print(g.status())


def cmd_job(args):
    from .jobs.pipeline import create_job
    cfg, db = _ctx(args)
    jid = create_job(db, building_id=args.building, priority=args.priority, experiment=args.experiment)
    print(f"작업 {jid} 생성")


def cmd_worker(args):
    from .jobs.worker import Worker
    cfg, db = _ctx(args)
    w = Worker(cfg, db, roles=args.roles.split(","))
    if args.once:
        while w.run_one():
            pass
    else:
        w.loop()


def cmd_run(args):
    """스케줄러 + 작업자 + 대시보드를 한 프로세스에서 실행."""
    from .dashboard.app import serve
    from .jobs.worker import Scheduler, Worker
    cfg, db = _ctx(args)
    sch = Scheduler(cfg, db)
    threading.Thread(target=sch.loop, daemon=True).start()
    for i in range(max(1, int(cfg.get("jobs.workers")))):
        roles = ["capture", "edit", "upload"] if i == 0 else ["edit", "upload"]
        threading.Thread(target=Worker(cfg, db, roles=roles, name=f"w{i}").loop, daemon=True).start()
    serve(cfg, db)


def cmd_dashboard(args):
    from .dashboard.app import serve
    cfg, db = _ctx(args)
    serve(cfg, db)


def cmd_youtube_auth(args):
    from .youtube.client import connect_account
    cfg, db = _ctx(args)
    info = connect_account(cfg)
    db.kv_set("youtube_channel", info)
    print(f"연결된 채널: {info}")


def cmd_analytics(args):
    from .analysis.performance import summarize
    from .youtube.client import collect_analytics
    cfg, db = _ctx(args)
    if not args.offline:
        collect_analytics(cfg, db)
    print(json.dumps(summarize(db), ensure_ascii=False, indent=1))


def cmd_server(args):
    from .builder.server import ServerManager
    cfg, db = _ctx(args)
    s = ServerManager(cfg, db)
    {"start": s.start, "stop": s.stop, "backup": s.backup, "restart": s.restart,
     "configure": lambda: s.write_properties(new_world=args.new_world)}[args.action]()
    if args.action == "start":
        print("서버 실행 중. Ctrl+C 로 종료")
        try:
            s.proc.wait()
        except KeyboardInterrupt:
            s.stop()


def cmd_music(args):
    from .audio.library import scan_library
    cfg, db = _ctx(args)
    if args.action == "fetch":
        from .audio.fetcher import AudioFetcher
        f = AudioFetcher(cfg, db)
        if args.mood:
            print(f"수집: {len(f.fetch_music(args.mood, args.count))}곡")
        else:
            print(f.ensure_library())
    ok, rej = scan_library(cfg)
    print(f"사용 가능 {len(ok)}곡")
    for t in ok:
        print(f"  {t.path.name}  [{t.license}] moods={t.moods}")
    for name, why in rej:
        print(f"  제외: {name} - {why}")


def cmd_trends(args):
    from .youtube.trends import update_trends
    cfg, db = _ctx(args)
    print(json.dumps(update_trends(cfg, db), ensure_ascii=False, indent=1))


def cmd_cleanup(args):
    from .jobs.support import cleanup
    cfg, db = _ctx(args)
    print(cleanup(cfg, db, aggressive=args.aggressive))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="mcbuildai", description="Minecraft 건축 타임랩스 자동 제작 시스템")
    ap.add_argument("--config", default="config.yaml")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    p = sub.add_parser("plan"); p.add_argument("--days", type=int, default=7); p.add_argument("--per-day", type=int, default=1)
    p.add_argument("--start"); p.add_argument("--seed", type=int); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("generate"); p.add_argument("--prompt"); p.add_argument("--image")
    p.add_argument("--variant-of", type=int); p.add_argument("--engine", default="auto", choices=["auto", "genmc", "procedural"])
    p.add_argument("--scale"); p.add_argument("--seed", type=int); p.set_defaults(fn=cmd_generate)
    p = sub.add_parser("genmc"); p.add_argument("action", choices=["pair", "status"]); p.add_argument("code", nargs="?")
    p.set_defaults(fn=cmd_genmc)
    p = sub.add_parser("job"); p.add_argument("--building", type=int); p.add_argument("--priority", type=int, default=5)
    p.add_argument("--experiment", action="store_true"); p.set_defaults(fn=cmd_job)
    p = sub.add_parser("worker"); p.add_argument("--roles", default="capture,edit,upload"); p.add_argument("--once", action="store_true")
    p.set_defaults(fn=cmd_worker)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    sub.add_parser("youtube-auth").set_defaults(fn=cmd_youtube_auth)
    p = sub.add_parser("analytics"); p.add_argument("--offline", action="store_true"); p.set_defaults(fn=cmd_analytics)
    p = sub.add_parser("server"); p.add_argument("action", choices=["start", "stop", "restart", "backup", "configure"])
    p.add_argument("--new-world", action="store_true"); p.set_defaults(fn=cmd_server)
    p = sub.add_parser("music"); p.add_argument("action", nargs="?", default="list", choices=["list", "fetch"])
    p.add_argument("--mood"); p.add_argument("--count", type=int, default=3); p.set_defaults(fn=cmd_music)
    sub.add_parser("trends").set_defaults(fn=cmd_trends)
    p = sub.add_parser("cleanup"); p.add_argument("--aggressive", action="store_true"); p.set_defaults(fn=cmd_cleanup)
    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
