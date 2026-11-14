"""제목, 설명, 해시태그 자동 생성 (영어 기본, 한국어 현지화 메타데이터 선택).

AI 사용 가능 시 Claude 가 쓰고, 아니면 규칙 기반 문구를 쓴다.
"""
from __future__ import annotations

from mcbuildai import i18n
from mcbuildai.config import Config
from mcbuildai.planner.ai_client import AIClient
from mcbuildai.planner.dna import BUILDING_TYPES, STYLES, TERRAINS, BuildingDNA

BASE_TAGS_EN = ["minecraft", "minecraftbuild", "minecrafttimelapse", "timelapse", "minecraftbuilding", "shorts"]
BASE_TAGS_KO = ["마인크래프트", "마크건축", "타임랩스"]


def _clean_tags(tags) -> list[str]:
    out = []
    for t in tags:
        t = str(t).lstrip("#").replace(" ", "")
        if t and t.lower() not in [o.lower() for o in out]:
            out.append(t)
    return out


def generate_metadata(cfg: Config, name: str, concept: str, dna: BuildingDNA, block_count: int,
                      credits: list[str] | None, variant: str = "short") -> dict:
    """variant: short(세로 쇼츠) / long(가로 편집본)"""
    ai = AIClient(cfg)
    en = i18n.lang(cfg) == "en"
    type_en, style_en = i18n.TYPE_EN[dna.btype], i18n.STYLE_EN[dna.style]
    trend = cfg.get("_runtime.trend_keywords") or []
    facts = (f"Build name: {name}\nConcept: {concept}\nType: {type_en}\nStyle: {style_en}\n"
             f"Terrain: {i18n.TERRAIN_EN[dna.terrain]}\nBlocks: {block_count:,}\nFormat: "
             f"{'YouTube Shorts (vertical, under 60s)' if variant == 'short' else 'regular landscape video'}")
    data = None
    if ai.available():
        try:
            lang_line = "Write in natural English." if en else "한국어로 쓴다."
            data = ai.json(
                "You run a Minecraft build timelapse YouTube channel. No emojis, no clickbait lies, no ALL CAPS sentences. "
                + lang_line,
                f"{facts}\n" + (f"Popular keywords right now: {', '.join(trend[:15])}\n" if trend else "")
                + "Write: a title (max 70 characters, curiosity-driven but accurate, without #Shorts), a description "
                  "(3 short sentences), 10 hashtags (no #, no spaces). "
                + ("Also translate the title and description into Korean. " if en and cfg.get("content.localize_ko") else "")
                + 'Format: {"title": "...", "description": "...", "hashtags": ["..."], "title_ko": "...", "description_ko": "..."}',
                max_tokens=1000)
        except Exception:
            data = None
    if not data:
        if en:
            data = {
                "title": f"Building a {style_en} {type_en} in Minecraft | {name}",
                "description": f"{concept}\n{block_count:,} blocks, placed one by one in a single timelapse.",
                "hashtags": [f"{style_en.lower()}build", f"minecraft{type_en.lower().replace(' ', '')}"],
                "title_ko": f"마인크래프트 {STYLES[dna.style]['ko']} {BUILDING_TYPES[dna.btype]} 건축 | {name}",
                "description_ko": f"{block_count:,}개의 블록으로 지은 {STYLES[dna.style]['ko']} 양식 "
                                  f"{BUILDING_TYPES[dna.btype]} 타임랩스입니다.",
            }
        else:
            data = {
                "title": f"{name} | 마인크래프트 {STYLES[dna.style]['ko']} {BUILDING_TYPES[dna.btype]} 타임랩스",
                "description": f"{concept}\n총 {block_count:,}개의 블록으로 지은 {STYLES[dna.style]['ko']} 양식 "
                               f"{BUILDING_TYPES[dna.btype]}입니다.",
                "hashtags": [BUILDING_TYPES[dna.btype].replace(" ", ""), STYLES[dna.style]["ko"] + "건축"],
            }
    tags = _clean_tags(list(data.get("hashtags", [])) + (BASE_TAGS_EN if en else BASE_TAGS_KO + BASE_TAGS_EN))
    if variant != "short":
        tags = [t for t in tags if t.lower() != "shorts"]
    title = data["title"].strip()
    if variant == "short" and "#shorts" not in title.lower():
        title = (title[:90] + " #Shorts").strip()
    credit_block = ""
    if credits:
        credit_block = "\n\n" + "\n".join(credits)
    desc = data["description"].strip() + "\n\n" + " ".join(f"#{t}" for t in tags[:15]) + credit_block
    out = {"title": title[:100], "description": desc[:5000], "tags": tags[:30],
           "language": "en" if en else "ko", "localizations": {}}
    if en and cfg.get("content.localize_ko") and data.get("title_ko"):
        tko = data["title_ko"].strip()
        if variant == "short" and "#shorts" not in tko.lower():
            tko = (tko[:90] + " #Shorts").strip()
        out["localizations"]["ko"] = {"title": tko[:100],
                                      "description": (data.get("description_ko", "").strip() + credit_block)[:5000]}
    return out
