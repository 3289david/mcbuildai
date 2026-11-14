"""타임랩스 연출 12종 정의와 자동 선택, 조합, 속도 계산."""
from __future__ import annotations

import random
from dataclasses import dataclass

from mcbuildai.db import DB


@dataclass(frozen=True)
class TimelapseStyle:
    id: str
    ko: str
    order: str            # builder.order 의 배치 순서 전략
    speed_profile: str    # constant / burst / ease_in / ease_out
    speed_mult: float     # 기본 속도 배율
    use_fill: bool        # 같은 블록 연속 구간을 fill 로 압축 (초고속)
    edit: str             # 편집 시 특수 처리: none / explosive / reverse


STYLES: dict[str, TimelapseStyle] = {s.id: s for s in [
    TimelapseStyle("fast", "Fast Build (초고속 건축)", "layer", "constant", 4.0, True, "none"),
    TimelapseStyle("normal", "Normal Build (일반 타임랩스)", "natural", "constant", 1.0, False, "none"),
    TimelapseStyle("layer", "Layer Build (층별 건축)", "layer", "constant", 1.0, False, "none"),
    TimelapseStyle("wave", "Wave Build (한 방향 건축)", "wave", "constant", 1.2, False, "none"),
    TimelapseStyle("random", "Random Build (여러 위치 동시 건축)", "multi_front", "constant", 1.2, False, "none"),
    TimelapseStyle("bottom_up", "Bottom Up (아래에서 위로)", "bottom_up", "ease_in", 1.0, False, "none"),
    TimelapseStyle("top_down", "Top Down (위에서 아래로)", "top_down", "constant", 1.0, False, "none"),
    TimelapseStyle("section", "Section Build (구역별 건축)", "section", "burst", 1.2, False, "none"),
    TimelapseStyle("material", "Material Build (재료별 순차 건축)", "material", "burst", 1.2, False, "none"),
    TimelapseStyle("explosive", "Explosive Reveal (극적인 공개)", "natural", "ease_in", 1.5, False, "explosive"),
    TimelapseStyle("reverse", "Reverse Reveal (역방향 연출)", "natural", "constant", 1.2, False, "reverse"),
    TimelapseStyle("sequential", "Sequential Build (순서대로 등장)", "sequential", "burst", 2.0, False, "none"),
]}


def recent_styles(db: DB, n: int = 4) -> list[str]:
    rows = db.query("SELECT timelapse_style FROM buildings WHERE timelapse_style IS NOT NULL ORDER BY id DESC LIMIT ?", (n,))
    return [r["timelapse_style"] for r in rows]


def choose_timelapse_style(db: DB, rng: random.Random, weights: dict[str, float] | None = None,
                           avoid: list[str] | None = None) -> str:
    """최근 사용 연출을 피하고, 성과 가중치를 반영해 고른다 (영상별 연출 중복 방지)."""
    from mcbuildai.analysis.performance import category_weights
    w = weights or category_weights(db).get("timelapse_style", {})
    recent = (avoid or []) + recent_styles(db)
    options = [s for s in STYLES if s not in recent[:3]] or list(STYLES)
    ws = [max(0.05, w.get(s, 1.0)) for s in options]
    return rng.choices(options, weights=ws, k=1)[0]


def combo(rng: random.Random, base: str) -> list[str]:
    """연출 조합: 본 건축 연출 + 보조 연출(마무리 구간)."""
    second = rng.choice([s for s in ("layer", "wave", "section", "material") if s != base])
    return [base, second]


def plan_build_duration(block_count: int, style_id: str, base_bps: float,
                        min_sec: float = 60, max_sec: float = 1800) -> tuple[float, float]:
    """(실제 건축 시간[초], 초당 블록 수). 영상 길이에 맞추기 위해 실제 촬영 시간을 범위 안으로 조절한다."""
    st = STYLES[style_id]
    bps = base_bps * st.speed_mult
    dur = block_count / bps
    if dur > max_sec:
        bps = block_count / max_sec
        dur = max_sec
    elif dur < min_sec:
        bps = max(1.0, block_count / min_sec)
        dur = block_count / bps
    return dur, bps


def speed_at(profile: str, progress: float) -> float:
    """진행률(0~1)에 따른 속도 배율 (평균이 대략 1)."""
    if profile == "ease_in":
        return 0.5 + progress * 1.0
    if profile == "ease_out":
        return 1.5 - progress * 1.0
    return 1.0
