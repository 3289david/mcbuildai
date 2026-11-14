"""건축물 생성 서비스: genMC(기본) 또는 절차적 생성기로 블록 모델을 만들고 파일과 DB 에 저장한다."""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from mcbuildai.config import Config
from mcbuildai.db import DB, now_iso
from mcbuildai.planner.dna import BUILDING_TYPES, SCALES, STYLES, TERRAINS, BuildingDNA
from .classify import auto_label, normalize
from .palette import PALETTES
from .preview import render_preview
from .schematic import write_mcfunction, write_schem
from .terrain import build_terrain
from .voxel import VoxelModel


def slugify(s: str) -> str:
    s = re.sub(r"[^\w가-힣]+", "_", s, flags=re.U).strip("_")
    return s[:40] or "build"


def estimate(model: VoxelModel, cfg: Config) -> tuple[float, float]:
    """(난이도 1~10, 예상 건축 시간[분]) — 블록 수, 재료 종류, 높이 기반 추정치."""
    n = len(model)
    mats = len(model.materials())
    h = model.size()[1]
    diff = 1.4 * math.log10(max(n, 10)) - 2 + mats / 12 + h / 45
    diff = round(max(1.0, min(10.0, diff)), 1)
    bps = float(cfg.get("build.blocks_per_second", 400))
    minutes = round(n / bps / 60, 1)
    return diff, minutes


def brief_from_dna(dna: BuildingDNA, name: str, concept: str, user_prompt: str | None = None) -> str:
    P = PALETTES[dna.palette]
    lines = [
        f"건축물 이름: {name}",
        f"종류: {BUILDING_TYPES[dna.btype]} ({dna.btype})",
        f"양식: {STYLES[dna.style]['ko']} ({dna.style})" + (f", 보조 양식 혼합: {dna.secondary_style}" if dna.secondary_style else ""),
        f"규모: {SCALES[dna.scale]['ko']} — 바닥 약 {dna.width} x {dna.depth} 블록, {dna.floors}층 이상",
        f"지붕: {dna.roof}, 창문 패턴: {dna.window_pattern}, 장식 밀도 {dna.ornament}/3, "
        f"{'대칭' if dna.symmetry == 'symmetric' else '비대칭'} 구조",
        "주요 재료: " + ", ".join(f"{k}={v.split(':')[-1]}" for k, v in P.items() if k != "ko"),
        f"주변 지형: {TERRAINS[dna.terrain]} ({dna.terrain}) — 건물 둘레에 조경 포함",
        f"내부 공간: {'층별 방, 계단, 조명 포함' if dna.interior else '외관 위주'}",
        f"콘셉트: {concept}",
    ]
    if user_prompt:
        lines.append(f"사용자 요청: {user_prompt}")
    return "\n".join(lines)


def save_building_files(model: VoxelModel, work_dir: Path, cfg: Config) -> dict:
    work_dir.mkdir(parents=True, exist_ok=True)
    model.to_json(work_dir / "model.json")
    schem = write_schem(model, work_dir / "building.schem", mc_version=cfg.get("genmc.export_version", "1.21.1"))
    write_mcfunction(model, work_dir / "building.mcfunction")
    preview = render_preview(model, work_dir / "preview.png", size=1024)
    (work_dir / "materials.json").write_text(json.dumps(model.materials(), ensure_ascii=False, indent=1),
                                             encoding="utf-8")
    return {"schematic_path": str(schem), "preview_path": str(preview)}


def create_building(cfg: Config, db: DB, dna: BuildingDNA, name: str, concept: str,
                    engine: str = "auto", user_prompt: str | None = None,
                    images: list[Path] | None = None, base_building_id: int | None = None) -> int:
    dna.validate()
    dna.resolve_dimensions()
    bid = db.execute(
        "INSERT INTO buildings(created_at, name, dna_json, dna_hash, btype, style, scale, palette, concept, "
        "timelapse_style, camera_style, status) VALUES (?,?,?,?,?,?,?,?,?,?,?, 'generating')",
        (now_iso(), name, dna.to_json(), dna.dna_hash(), dna.btype, dna.style, dna.scale, dna.palette,
         concept, dna.timelapse_style, dna.camera_style))
    work_dir = cfg.path_of("paths.output_dir") / "buildings" / f"{bid:05d}_{slugify(name)}"
    meta: dict = {"engine": None}
    try:
        model = None
        if engine in ("auto", "genmc"):
            from mcbuildai.planner.ai_client import AIClient
            from .genmc import GenMC, GenMCDesigner, GenMCError
            ai = AIClient(cfg)
            gm = GenMC(cfg, db)
            if ai.available() and gm.resume_key:
                try:
                    base_name = None
                    if base_building_id:
                        r = db.one("SELECT name FROM buildings WHERE id=?", (base_building_id,))
                        base_name = r["name"] if r else None
                    res = GenMCDesigner(cfg, db, ai, gm).design(
                        brief_from_dna(dna, name, concept, user_prompt), name, work_dir, images=images,
                        base_project=base_name)
                    model = auto_label(normalize(res.model))
                    if not any(v.part == "terrain" for v in model.blocks.values()):
                        import random
                        terrain = build_terrain(model, dna, random.Random(dna.seed))
                        terrain.merge(model)
                        model = terrain
                    meta.update(engine="genmc", genmc_summary=res.summary, genmc_parts=res.parts,
                                genmc_skipped_lines=res.skipped_lines,
                                genmc_render=str(res.preview_png) if res.preview_png else None)
                except GenMCError as e:
                    if engine == "genmc":
                        raise
                    db.log("WARN", "generator", f"genMC 사용 불가, 절차적 생성기로 대체: {e}")
            elif engine == "genmc":
                raise RuntimeError("genMC 를 쓰려면 ANTHROPIC_API_KEY 와 genMC 페어링이 필요합니다")
        if model is None:
            from .building import generate
            model = generate(dna)
            meta["engine"] = "procedural"
        files = save_building_files(model, work_dir, cfg)
        diff, minutes = estimate(model, cfg)
        (work_dir / "meta.json").write_text(json.dumps({**meta, "dna": json.loads(dna.to_json()), "name": name,
                                                        "concept": concept}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        db.execute("UPDATE buildings SET block_count=?, difficulty=?, est_minutes=?, schematic_path=?, "
                   "preview_path=?, status='generated' WHERE id=?",
                   (len(model), diff, minutes, files["schematic_path"],
                    meta.get("genmc_render") or files["preview_path"], bid))
        db.log("INFO", "generator", f"[{bid}] {name} 생성 완료 ({meta['engine']}, {len(model)} 블록)")
        return bid
    except Exception as e:
        db.execute("UPDATE buildings SET status='failed' WHERE id=?", (bid,))
        db.log("ERROR", "generator", f"[{bid}] 생성 실패: {e}")
        raise


def building_dir(cfg: Config, db: DB, bid: int) -> Path:
    r = db.one("SELECT schematic_path FROM buildings WHERE id=?", (bid,))
    if not r or not r["schematic_path"]:
        raise FileNotFoundError(f"건축물 {bid} 파일이 없습니다")
    return Path(r["schematic_path"]).parent


def load_model(cfg: Config, db: DB, bid: int) -> VoxelModel:
    return VoxelModel.from_json(building_dir(cfg, db, bid) / "model.json")
