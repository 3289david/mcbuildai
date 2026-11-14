"""건축물 DNA: 형태, 양식, 규모, 재료, 연출 조합을 하나의 레코드로 표현한다.

DNA 해시로 이전 건축물과의 중복을 막고, 변형(mutate)으로 기존 건축물을 재설계한다.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------------------
# 카탈로그
# ---------------------------------------------------------------------------
BUILDING_TYPES: dict[str, str] = {
    "house": "주택",
    "mansion": "저택",
    "tower": "탑",
    "castle": "성",
    "temple": "신전",
    "cathedral": "대성당",
    "skyscraper": "고층 빌딩",
    "pagoda": "목탑",
    "lighthouse": "등대",
    "ziggurat": "지구라트",
    "hanok": "한옥",
    "citadel": "초대형 요새",
}

STYLES: dict[str, dict] = {
    "medieval":  {"ko": "중세",     "palettes": ["oak_cobble", "stone_castle", "spruce_stone"], "roofs": ["gable", "hip", "spire"]},
    "gothic":    {"ko": "고딕",     "palettes": ["deepslate_gothic", "stone_castle", "blackstone_dark"], "roofs": ["spire", "gable"]},
    "classical": {"ko": "고전",     "palettes": ["white_marble", "sandstone_desert"], "roofs": ["gable", "dome", "flat"]},
    "japanese":  {"ko": "일본",     "palettes": ["japanese_dark", "cherry_blossom"], "roofs": ["pagoda", "hip"]},
    "chinese":   {"ko": "중국",     "palettes": ["red_imperial"], "roofs": ["pagoda", "hip"]},
    "korean":    {"ko": "한국 전통", "palettes": ["hanok_giwa"], "roofs": ["curved", "hip"]},
    "modern":    {"ko": "모던",     "palettes": ["quartz_modern", "concrete_gray"], "roofs": ["flat"]},
    "futuristic": {"ko": "미래",    "palettes": ["copper_future", "prismarine_atlantis"], "roofs": ["flat", "dome", "spire"]},
    "fantasy":   {"ko": "판타지",   "palettes": ["purpur_fantasy", "prismarine_atlantis", "cherry_blossom"], "roofs": ["spire", "dome"]},
    "desert":    {"ko": "사막",     "palettes": ["sandstone_desert", "mud_adobe"], "roofs": ["flat", "dome"]},
    "nordic":    {"ko": "북유럽",   "palettes": ["spruce_stone", "oak_cobble"], "roofs": ["gable", "hip"]},
    "steampunk": {"ko": "스팀펑크", "palettes": ["brick_copper"], "roofs": ["gable", "dome", "flat"]},
}

# 건축물 종류별로 어울리는 양식
TYPE_STYLES: dict[str, list[str]] = {
    "house": ["medieval", "nordic", "modern", "japanese", "desert", "steampunk", "fantasy"],
    "mansion": ["classical", "medieval", "modern", "steampunk", "gothic"],
    "tower": ["medieval", "gothic", "fantasy", "futuristic", "steampunk"],
    "castle": ["medieval", "gothic", "fantasy"],
    "temple": ["classical", "desert", "fantasy", "japanese"],
    "cathedral": ["gothic", "classical", "fantasy"],
    "skyscraper": ["modern", "futuristic", "steampunk"],
    "pagoda": ["japanese", "chinese", "korean"],
    "lighthouse": ["medieval", "nordic", "modern", "fantasy"],
    "ziggurat": ["desert", "fantasy", "futuristic"],
    "hanok": ["korean"],
    "citadel": ["medieval", "gothic", "fantasy", "futuristic"],
}

SCALES: dict[str, dict] = {
    # footprint 범위(블록), 층 높이 기본
    "small":  {"ko": "소형",   "span": (9, 15),  "floors": (1, 2)},
    "medium": {"ko": "중형",   "span": (15, 25), "floors": (2, 3)},
    "large":  {"ko": "대형",   "span": (25, 41), "floors": (3, 5)},
    "huge":   {"ko": "초대형", "span": (41, 71), "floors": (4, 8)},
    "mega":   {"ko": "메가",   "span": (71, 121), "floors": (6, 12)},
}

# 종류별 허용 규모
TYPE_SCALES: dict[str, list[str]] = {
    "house": ["small", "medium"],
    "mansion": ["medium", "large"],
    "tower": ["small", "medium", "large"],
    "castle": ["large", "huge", "mega"],
    "temple": ["medium", "large", "huge"],
    "cathedral": ["large", "huge"],
    "skyscraper": ["medium", "large", "huge"],
    "pagoda": ["small", "medium", "large"],
    "lighthouse": ["small", "medium"],
    "ziggurat": ["large", "huge", "mega"],
    "hanok": ["medium", "large"],
    "citadel": ["huge", "mega"],
}

TERRAINS: dict[str, str] = {
    "plains": "평원", "island": "섬", "mountain": "산", "sea": "바다",
    "desert": "사막", "snow": "설원", "forest": "숲", "lava": "용암 지대",
}

TIMES = {"morning": 0, "noon": 6000, "sunset": 12000, "night": 18000}
WEATHERS = ["clear", "rain", "thunder", "snow"]

WINDOW_PATTERNS = ["grid", "tall", "band", "sparse", "arched"]


@dataclass
class BuildingDNA:
    btype: str
    style: str
    scale: str
    palette: str
    roof: str
    symmetry: str = "symmetric"          # symmetric / asymmetric
    secondary_style: str | None = None   # 양식 혼합
    window_pattern: str = "grid"
    ornament: int = 1                    # 0~3 장식 밀도
    terrain: str = "plains"
    interior: bool = True
    timelapse_style: str = "normal"
    camera_style: str = "orbit_360"
    time_of_day: str = "noon"
    weather: str = "clear"
    event: str | None = None
    seed: int = field(default_factory=lambda: random.randrange(1, 2**31))
    width: int = 0                       # 0이면 규모에서 자동 결정
    depth: int = 0
    floors: int = 0

    def resolve_dimensions(self) -> None:
        rng = random.Random(self.seed)
        lo, hi = SCALES[self.scale]["span"]
        if not self.width:
            self.width = rng.randrange(lo, hi + 1) | 1      # 홀수로 맞춰 중심축 확보
        if not self.depth:
            ratio = 1.0 if self.symmetry == "symmetric" and self.btype in ("tower", "pagoda", "lighthouse", "ziggurat", "citadel", "castle") else rng.uniform(0.6, 1.0)
            self.depth = max(lo, int(self.width * ratio)) | 1
        if not self.floors:
            f_lo, f_hi = SCALES[self.scale]["floors"]
            self.floors = rng.randint(f_lo, f_hi)

    def key_fields(self) -> dict:
        """중복 판정에 쓰는 핵심 필드. 시드와 연출은 제외한다."""
        return {
            "btype": self.btype, "style": self.style, "scale": self.scale,
            "palette": self.palette, "roof": self.roof, "symmetry": self.symmetry,
            "secondary_style": self.secondary_style, "terrain": self.terrain,
            "window_pattern": self.window_pattern,
        }

    def dna_hash(self) -> str:
        raw = json.dumps(self.key_fields(), sort_keys=True)
        return hashlib.sha1(raw.encode()).hexdigest()[:16]

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str | dict) -> "BuildingDNA":
        d = json.loads(s) if isinstance(s, str) else dict(s)
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def similarity(self, other: "BuildingDNA") -> float:
        a, b = self.key_fields(), other.key_fields()
        return sum(1 for k in a if a[k] == b[k]) / len(a)

    def validate(self) -> None:
        if self.btype not in BUILDING_TYPES:
            raise ValueError(f"알 수 없는 건축물 종류: {self.btype}")
        if self.style not in STYLES:
            raise ValueError(f"알 수 없는 양식: {self.style}")
        if self.scale not in SCALES:
            raise ValueError(f"알 수 없는 규모: {self.scale}")
        from mcbuildai.generator.palette import PALETTES
        if self.palette not in PALETTES:
            raise ValueError(f"알 수 없는 팔레트: {self.palette}")
        if self.terrain not in TERRAINS:
            raise ValueError(f"알 수 없는 지형: {self.terrain}")


def random_dna(rng: random.Random, weights: dict[str, dict[str, float]] | None = None,
               forced: dict | None = None) -> BuildingDNA:
    """가중치(성과 분석 결과)를 반영해 DNA 하나를 뽑는다."""
    weights = weights or {}
    forced = forced or {}

    def pick(category: str, options: list[str]) -> str:
        if category in forced and forced[category] in options:
            return forced[category]
        w = [max(0.05, weights.get(category, {}).get(o, 1.0)) for o in options]
        return rng.choices(options, weights=w, k=1)[0]

    btype = pick("btype", list(BUILDING_TYPES))
    style = pick("style", TYPE_STYLES[btype])
    scale = pick("scale", TYPE_SCALES[btype])
    palette = pick("palette", STYLES[style]["palettes"])
    roof_opts = STYLES[style]["roofs"]
    if btype in ("pagoda",):
        roof_opts = ["pagoda"]
    elif btype == "hanok":
        roof_opts = ["curved"]
    elif btype == "skyscraper":
        roof_opts = [r for r in roof_opts if r in ("flat", "spire", "dome")] or ["flat"]
    elif btype == "lighthouse":
        roof_opts = ["dome", "spire"]
    elif btype == "ziggurat":
        roof_opts = ["flat"]
    roof = pick("roof", roof_opts)
    terrain = pick("terrain", list(TERRAINS))
    symmetry = pick("symmetry", ["symmetric", "asymmetric"])
    secondary = None
    if rng.random() < 0.25:
        secondary = rng.choice([s for s in STYLES if s != style])
    dna = BuildingDNA(
        btype=btype, style=style, scale=scale, palette=palette, roof=roof,
        symmetry=symmetry, secondary_style=secondary,
        window_pattern=pick("window_pattern", WINDOW_PATTERNS),
        ornament=rng.randint(0, 3), terrain=terrain,
        interior=scale not in ("mega",),
        time_of_day=pick("time_of_day", list(TIMES)),
        weather=pick("weather", WEATHERS if terrain == "snow" else ["clear", "clear", "rain", "thunder"]),
        seed=rng.randrange(1, 2**31),
    )
    for k, v in forced.items():
        if hasattr(dna, k) and k not in ("btype", "style", "scale", "palette", "roof", "terrain", "symmetry"):
            setattr(dna, k, v)
    dna.resolve_dimensions()
    return dna


def mutate(dna: BuildingDNA, rng: random.Random, strength: float = 0.4) -> BuildingDNA:
    """기존 건축물 변형 및 재설계: 일부 유전자를 바꿔 새 DNA를 만든다."""
    d = BuildingDNA.from_json(dna.to_json())
    d.seed = rng.randrange(1, 2**31)
    if rng.random() < strength:
        d.palette = rng.choice(STYLES[d.style]["palettes"] + [p for s in STYLES.values() for p in s["palettes"]][:3])
    if rng.random() < strength:
        d.roof = rng.choice(STYLES[d.style]["roofs"])
    if rng.random() < strength:
        d.window_pattern = rng.choice(WINDOW_PATTERNS)
    if rng.random() < strength:
        d.symmetry = "asymmetric" if d.symmetry == "symmetric" else "symmetric"
    if rng.random() < strength:
        d.terrain = rng.choice(list(TERRAINS))
    if rng.random() < strength:
        d.secondary_style = rng.choice([s for s in STYLES if s != d.style])
    d.ornament = max(0, min(3, d.ornament + rng.choice([-1, 0, 1])))
    d.width = d.depth = d.floors = 0
    d.resolve_dimensions()
    return d
