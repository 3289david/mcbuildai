"""영상에 들어가는 문구 (영어 기본, 한국어 선택). config: content.language = en | ko"""
from __future__ import annotations

TYPE_EN = {
    "house": "House", "mansion": "Mansion", "tower": "Tower", "castle": "Castle", "temple": "Temple",
    "cathedral": "Cathedral", "skyscraper": "Skyscraper", "pagoda": "Pagoda", "lighthouse": "Lighthouse",
    "ziggurat": "Ziggurat", "hanok": "Hanok", "citadel": "Mega Citadel",
}
STYLE_EN = {
    "medieval": "Medieval", "gothic": "Gothic", "classical": "Classical", "japanese": "Japanese",
    "chinese": "Chinese", "korean": "Korean", "modern": "Modern", "futuristic": "Futuristic",
    "fantasy": "Fantasy", "desert": "Desert", "nordic": "Nordic", "steampunk": "Steampunk",
}
SCALE_EN = {"small": "Small", "medium": "Medium", "large": "Large", "huge": "Huge", "mega": "Mega"}
TERRAIN_EN = {"plains": "plains", "island": "island", "mountain": "mountains", "sea": "open sea",
              "desert": "desert", "snow": "snowfield", "forest": "forest", "lava": "lava fields"}

STRINGS = {
    "en": {
        "complete": "COMPLETE",
        "before": "BEFORE",
        "after": "AFTER",
        "blocks": "BLOCKS",
        "speed": "{x}x SPEED",
        "hook": "Building a {style} {type}",
        "hook_sub": "{blocks} blocks",
        "cta": "Which build should I make next?",
        "cta_sub": "Comment below",
        "music_credit": "Music",
        "sfx_credit": "Sound effects",
    },
    "ko": {
        "complete": "완성",
        "before": "BEFORE",
        "after": "AFTER",
        "blocks": "블록",
        "speed": "{x}배속",
        "hook": "{style} {type} 짓기",
        "hook_sub": "블록 {blocks}개",
        "cta": "다음엔 뭘 지을까요?",
        "cta_sub": "댓글로 알려주세요",
        "music_credit": "음악",
        "sfx_credit": "효과음",
    },
}

FALLBACK_NAME_EN = ["Dawnspire", "Ironhollow", "Moonveil", "Stormkeep", "Amberhall", "Frostcrown", "Sunfall",
                    "Mistwood", "Starfall", "Emberreach", "Cloudhaven", "Tidewatch", "Ravenmoor", "Goldcrest",
                    "Silverpeak", "Ashenvale", "Duskmere", "Brightwater", "Thornwall", "Skyreach"]


def lang(cfg) -> str:
    v = (cfg.get("content.language", "en") or "en").lower()
    return v if v in STRINGS else "en"


def t(cfg, key: str, **kw) -> str:
    return STRINGS[lang(cfg)][key].format(**kw)


def type_name(cfg, btype: str) -> str:
    if lang(cfg) == "en":
        return TYPE_EN.get(btype, btype.title())
    from mcbuildai.planner.dna import BUILDING_TYPES
    return BUILDING_TYPES.get(btype, btype)


def style_name(cfg, style: str) -> str:
    if lang(cfg) == "en":
        return STYLE_EN.get(style, style.title())
    from mcbuildai.planner.dna import STYLES
    return STYLES[style]["ko"]
