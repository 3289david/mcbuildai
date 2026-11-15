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


def brief_from_dna(dna: BuildingDNA, name: str, concept: str, user_prompt: str | None = None,
                   min_blocks: int = 0) -> str:
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
    if min_blocks:
        lines.append(f"중요: 완성된 건축물은 반드시 블록 {min_blocks:,}개 이상이어야 한다. "
                     f"바닥/치수를 충분히 넓히고, 디테일(기둥, 장식, 내부 구조, 주변 조경)을 "
                     f"풍성하게 채워서 목표 블록 수를 넘기도록 설계해라.")
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
    min_blocks = int(cfg.get("build.min_blocks", 0))
    try:
        model = None
        if engine in ("auto", "genmc"):
            from mcbuildai.planner.ai_client import AIClient, AIUnavailable
            from .genmc import GenMC, GenMCDesigner, GenMCError
            # genMC 는 MCP 표준 서버라 특정 LLM 제공자에 종속되지 않는다
            # (https://genmc.app/docs/connect-agent: Claude/ChatGPT/Codex 등 모두 지원).
            # genmc.provider 를 명시하지 않으면 글로벌 ai.provider(기본 OpenRouter)를 그대로 써서
            # 같은 키로 genMC 를 운전한다 (전용 Anthropic 키가 따로 필요 없음).
            genmc_provider = cfg.get("genmc.provider") or cfg.get("ai.provider", "anthropic")
            if genmc_provider == "anthropic":
                key_env = cfg.get("genmc.anthropic_api_key_env", "ANTHROPIC_API_KEY")
                genmc_model = cfg.get("genmc.model") or "claude-sonnet-5-5"
            else:
                key_env = cfg.get("ai.api_key_env")
                # ai.model(gpt-5-nano)은 이름/콘셉트 생성용 저가 모델이라 genMC 의 복잡한 에이전트
                # 루프에는 약하다. OpenRouter 는 Claude 모델도 그대로 호출 가능하므로 같은 키로
                # 더 강한 모델을 쓴다.
                genmc_model = cfg.get("genmc.model") or "anthropic/claude-sonnet-4.5"
            ai = AIClient(cfg, provider=genmc_provider, api_key_env=key_env, model=genmc_model)
            gm = GenMC(cfg, db)
            if ai.available() and gm.resume_key:
                try:
                    base_name = None
                    if base_building_id:
                        r = db.one("SELECT name FROM buildings WHERE id=?", (base_building_id,))
                        base_name = r["name"] if r else None
                    res = GenMCDesigner(cfg, db, ai, gm).design(
                        brief_from_dna(dna, name, concept, user_prompt, min_blocks=min_blocks), name, work_dir,
                        images=images, base_project=base_name)
                    model = auto_label(normalize(res.model))
                    if not any(v.part == "terrain" for v in model.blocks.values()):
                        import random
                        terrain = build_terrain(model, dna, random.Random(dna.seed))
                        terrain.merge(model)
                        model = terrain
                    meta.update(engine="genmc", genmc_summary=res.summary, genmc_parts=res.parts,
                                genmc_skipped_lines=res.skipped_lines,
                                genmc_render=str(res.preview_png) if res.preview_png else None)
                    if min_blocks and len(model) < min_blocks:
                        db.log("WARN", "generator", f"[{bid}] genMC 결과가 목표 블록 수({min_blocks:,})에 "
                                                     f"못 미침: {len(model):,}")
                except (GenMCError, AIUnavailable) as e:
                    if engine == "genmc":
                        raise
                    db.log("WARN", "generator", f"genMC 사용 불가, 절차적 생성기로 대체: {e}")
            elif engine == "genmc":
                raise RuntimeError("genMC 를 쓰려면 ANTHROPIC_API_KEY 와 genMC 페어링이 필요합니다")
        if model is None:
            from .building import generate
            model = generate(dna)
            meta["engine"] = "procedural"
            attempts = 0
            while min_blocks and len(model) < min_blocks and attempts < 4:
                attempts += 1
                dna.width = int(dna.width * 1.35) | 1
                dna.depth = int(dna.depth * 1.35) | 1
                dna.floors = dna.floors + 2
                model = generate(dna)
            if min_blocks and len(model) < min_blocks:
                db.log("WARN", "generator", f"[{bid}] 절차적 생성기가 목표 블록 수({min_blocks:,})에 "
                                             f"못 미침 ({attempts}회 확장 후): {len(model):,}")
        files = save_building_files(model, work_dir, cfg)
        diff, minutes = estimate(model, cfg)
        (work_dir / "meta.json").write_text(json.dumps({**meta, "dna": json.loads(dna.to_json()), "name": name,
                                                        "concept": concept}, ensure_ascii=False, indent=1),
                                            encoding="utf-8")
        db.execute("UPDATE buildings SET block_count=?, difficulty=?, est_minutes=?, schematic_path=?, "
                   "preview_path=?, dna_json=?, status='generated' WHERE id=?",
                   (len(model), diff, minutes, files["schematic_path"],
                    meta.get("genmc_render") or files["preview_path"], dna.to_json(), bid))
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
