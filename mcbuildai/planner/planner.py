"""AI 콘텐츠 기획: 매일 새 건축 주제 선정, 중복 방지, 계절/이벤트 기획, 주간/월간 일정 생성."""
from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from mcbuildai.config import Config
from mcbuildai.db import DB, now_iso
from .ai_client import AIClient
from .dna import (BUILDING_TYPES, SCALES, STYLES, TERRAINS, BuildingDNA, random_dna)

# 날짜는 (월, 일) 범위. 음력 명절은 연도별 양력 날짜(설날/추석 당일)를 config 의 planner.lunar_dates 로 덮어쓸 수 있다.
DEFAULT_EVENTS = [
    {"id": "new_year", "ko": "새해", "start": "12-30", "end": "01-02",
     "force": {"time_of_day": "night", "btype": "tower"}},
    {"id": "spring_blossom", "ko": "벚꽃 시즌", "start": "03-25", "end": "04-15",
     "force": {"style": "japanese", "palette": "cherry_blossom", "time_of_day": "morning"}},
    {"id": "summer", "ko": "여름 바다", "start": "07-15", "end": "08-20",
     "force": {"terrain": "island", "weather": "clear"}},
    {"id": "halloween", "ko": "핼러윈", "start": "10-25", "end": "10-31",
     "force": {"style": "gothic", "palette": "blackstone_dark", "time_of_day": "night", "weather": "thunder"}},
    {"id": "christmas", "ko": "크리스마스", "start": "12-18", "end": "12-25",
     "force": {"style": "nordic", "terrain": "snow", "weather": "snow", "time_of_day": "sunset"}},
    {"id": "winter", "ko": "겨울", "start": "12-01", "end": "02-28",
     "force": {"terrain": "snow"}, "probability": 0.4},
]

# 설날/추석 당일 (양력). 앞뒤 3일을 이벤트 기간으로 쓴다. 사용 전 실제 달력과 확인하고 config 에서 수정 가능.
DEFAULT_LUNAR = {
    "seollal": ["2026-02-17", "2027-02-07", "2028-01-27", "2029-02-13", "2030-02-03"],
    "chuseok": ["2026-09-25", "2027-09-15", "2028-10-03", "2029-09-22", "2030-09-12"],
}
LUNAR_FORCE = {
    "seollal": {"ko": "설날", "force": {"btype": "hanok", "style": "korean", "terrain": "snow"}},
    "chuseok": {"ko": "추석", "force": {"btype": "hanok", "style": "korean", "time_of_day": "night"}},
}

FALLBACK_ADJ = ["새벽빛", "황혼의", "잊혀진", "구름 위의", "고요한", "푸른 바람의", "달빛", "황금빛",
                "안개 속의", "별이 내리는", "오래된", "바다를 품은", "숲속의", "붉은 노을의"]


def _in_range(d: date, start: str, end: str) -> bool:
    s = tuple(int(x) for x in start.split("-"))
    e = tuple(int(x) for x in end.split("-"))
    cur = (d.month, d.day)
    return s <= cur <= e if s <= e else (cur >= s or cur <= e)


@dataclass
class Plan:
    date: str
    dna: BuildingDNA
    name: str
    concept: str
    video_concept: str
    event: str | None
    difficulty_hint: str

    def to_json(self) -> str:
        return json.dumps({"date": self.date, "dna": json.loads(self.dna.to_json()), "name": self.name,
                           "concept": self.concept, "video_concept": self.video_concept,
                           "event": self.event, "difficulty_hint": self.difficulty_hint}, ensure_ascii=False)

    @classmethod
    def from_json(cls, s: str) -> "Plan":
        d = json.loads(s)
        return cls(date=d["date"], dna=BuildingDNA.from_json(d["dna"]), name=d["name"], concept=d["concept"],
                   video_concept=d.get("video_concept", ""), event=d.get("event"),
                   difficulty_hint=d.get("difficulty_hint", ""))


class Planner:
    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db
        self.ai = AIClient(cfg)

    # ------------------------------------------------------------------
    def events_for(self, d: date, rng: random.Random) -> dict | None:
        lunar = self.cfg.get("planner.lunar_dates", DEFAULT_LUNAR)
        for key, days in lunar.items():
            for ds in days:
                center = date.fromisoformat(ds)
                if abs((d - center).days) <= 3 and key in LUNAR_FORCE:
                    return {"id": key, **LUNAR_FORCE[key]}
        for ev in self.cfg.get("planner.events", DEFAULT_EVENTS):
            if _in_range(d, ev["start"], ev["end"]) and rng.random() < ev.get("probability", 1.0):
                return ev
        return None

    def recent_dnas(self, n: int = 30) -> list[BuildingDNA]:
        rows = self.db.query("SELECT dna_json FROM buildings WHERE status!='failed' ORDER BY id DESC LIMIT ?", (n,))
        planned = self.db.query("SELECT plan_json FROM schedule ORDER BY id DESC LIMIT ?", (n,))
        out = [BuildingDNA.from_json(r["dna_json"]) for r in rows]
        for r in planned:
            try:
                out.append(Plan.from_json(r["plan_json"]).dna)
            except Exception:
                pass
        return out

    def used_hashes(self) -> set[str]:
        hs = {r["dna_hash"] for r in self.db.query("SELECT dna_hash FROM buildings WHERE status!='failed'")}
        for r in self.db.query("SELECT plan_json FROM schedule"):
            try:
                hs.add(Plan.from_json(r["plan_json"]).dna.dna_hash())
            except Exception:
                pass
        return hs

    def category_weights(self) -> dict[str, dict[str, float]]:
        """성과 분석 가중치 x 최근 사용 감쇠."""
        from mcbuildai.analysis.performance import category_weights
        w = category_weights(self.db)
        # YouTube 인기 영상 키워드 가중치 (trends.py) 를 곱한다
        for cat, vals in (self.db.kv_get("trend_weights", {}) or {}).items():
            for val, tw in vals.items():
                w.setdefault(cat, {})
                w[cat][val] = w[cat].get(val, 1.0) * float(tw)
        recent = self.recent_dnas(10)
        for i, d in enumerate(recent):
            decay = 0.55 if i < 3 else 0.8
            for cat in ("btype", "style", "palette", "terrain", "roof"):
                val = getattr(d, cat)
                w.setdefault(cat, {})
                w[cat][val] = w[cat].get(val, 1.0) * decay
        return w

    # ------------------------------------------------------------------
    def plan_day(self, d: date, rng: random.Random | None = None, forced: dict | None = None) -> Plan:
        rng = rng or random.Random()
        ev = self.events_for(d, rng)
        force = dict(ev["force"]) if ev else {}
        force.update(forced or {})
        weights = self.category_weights()
        used = self.used_hashes()
        recent = self.recent_dnas(20)
        best, best_score = None, -1.0
        for _ in range(60):
            dna = random_dna(rng, weights, force)
            if dna.dna_hash() in used:
                continue
            sim = max((dna.similarity(r) for r in recent), default=0.0)
            score = (1 - sim) + rng.random() * 0.3
            if score > best_score:
                best, best_score = dna, score
        if best is None:
            # 조합이 고갈된 경우: 강제 조건을 풀고 다시 시도
            best = random_dna(rng, weights, {})
        from mcbuildai.timelapse.styles import choose_timelapse_style
        from mcbuildai.camera.paths import choose_camera_style
        recent_plans = self.recent_dnas(4)
        best.timelapse_style = choose_timelapse_style(self.db, rng, avoid=[d.timelapse_style for d in recent_plans])
        best.camera_style = choose_camera_style(self.db, rng, best.btype,
                                                avoid=[d.camera_style for d in recent_plans])
        best.event = ev["id"] if ev else None
        name, concept, video_concept = self.name_and_concept(best, ev, rng)
        hint = f"{SCALES[best.scale]['ko']} {BUILDING_TYPES[best.btype]}"
        return Plan(d.isoformat(), best, name, concept, video_concept, best.event, hint)

    def name_and_concept(self, dna: BuildingDNA, ev: dict | None, rng: random.Random) -> tuple[str, str, str]:
        """건축물 이름/콘셉트. 영상 언어(content.language)를 따른다 (기본 영어)."""
        from mcbuildai import i18n
        en = i18n.lang(self.cfg) == "en"
        desc = (f"type={i18n.TYPE_EN[dna.btype]}, style={i18n.STYLE_EN[dna.style]}, scale={dna.scale}, "
                f"terrain={i18n.TERRAIN_EN[dna.terrain]}, time={dna.time_of_day}, weather={dna.weather}, "
                f"timelapse={dna.timelapse_style}, camera={dna.camera_style}"
                + (f", event={ev['id']}" if ev else ""))
        trend = ", ".join(self.db.kv_get("trend_keywords", [])[:15])
        if self.ai.available():
            try:
                past = [r["name"] for r in self.db.query("SELECT name FROM buildings ORDER BY id DESC LIMIT 40")]
                if en:
                    data = self.ai.json(
                        "You plan a Minecraft build timelapse YouTube Shorts channel. Write natural English. No emojis.",
                        "Create a proper-noun style name (max 24 characters), a build concept (2 sentences) and a video "
                        f"concept (1 sentence) for this build.\n{desc}\n"
                        + (f"Currently popular keywords on YouTube: {trend}\n" if trend else "")
                        + f"Names already used (do not repeat): {', '.join(past)}\n"
                        'Format: {"name": "...", "concept": "...", "video_concept": "..."}', max_tokens=600)
                else:
                    data = self.ai.json(
                        "너는 Minecraft 건축 타임랩스 쇼츠 채널의 기획자다. 한국어로 답한다. 이모지를 쓰지 않는다.",
                        f"다음 건축물의 이름(12자 이내, 고유명사 느낌), 건축 콘셉트(2문장), 영상 콘셉트(1문장)를 만들어라.\n"
                        f"{desc}\n이미 쓴 이름(중복 금지): {', '.join(past)}\n"
                        '형식: {"name": "...", "concept": "...", "video_concept": "..."}', max_tokens=600)
                return data["name"].strip(), data["concept"].strip(), data["video_concept"].strip()
            except Exception as e:
                self.db.log("WARN", "planner", f"AI 이름 생성 실패, 기본 규칙 사용: {e}")
        if en:
            used = {r["name"] for r in self.db.query("SELECT name FROM buildings")}
            base = [n for n in i18n.FALLBACK_NAME_EN if not any(u.startswith(n) for u in used)] or i18n.FALLBACK_NAME_EN
            name = f"{rng.choice(base)} {i18n.TYPE_EN[dna.btype]}"
            concept = (f"A {dna.scale} {i18n.STYLE_EN[dna.style].lower()} {i18n.TYPE_EN[dna.btype].lower()} "
                       f"rising over the {i18n.TERRAIN_EN[dna.terrain]}.")
            video = f"{dna.timelapse_style} timelapse with a {dna.camera_style.replace('_', ' ')} camera."
            return name, concept, video
        adj = rng.choice(FALLBACK_ADJ)
        name = f"{adj} {STYLES[dna.style]['ko']} {BUILDING_TYPES[dna.btype]}"
        concept = (f"{TERRAINS[dna.terrain]} 위에 세운 {SCALES[dna.scale]['ko']} {STYLES[dna.style]['ko']} 양식의 "
                   f"{BUILDING_TYPES[dna.btype]}.")
        video = f"{dna.timelapse_style} 연출과 {dna.camera_style} 카메라로 완성까지 보여준다."
        return name, concept, video

    # ------------------------------------------------------------------
    def schedule(self, start: date, days: int, per_day: int = 1, seed: int | None = None) -> list[int]:
        """주간(7)/월간(30) 콘텐츠 일정 생성. schedule 테이블 id 목록을 반환."""
        rng = random.Random(seed)
        ids = []
        for i in range(days):
            d = start + timedelta(days=i)
            existing = self.db.query("SELECT id FROM schedule WHERE date=?", (d.isoformat(),))
            for _ in range(max(0, per_day - len(existing))):
                plan = self.plan_day(d, rng)
                ids.append(self.db.execute("INSERT INTO schedule(date, plan_json, created_at) VALUES (?,?,?)",
                                           (d.isoformat(), plan.to_json(), now_iso())))
        self.db.log("INFO", "planner", f"{start} 부터 {days}일 일정 {len(ids)}건 생성")
        return ids

    def plans_for(self, d: date) -> list[tuple[int, Plan]]:
        rows = self.db.query("SELECT id, plan_json FROM schedule WHERE date=? AND job_id IS NULL", (d.isoformat(),))
        return [(r["id"], Plan.from_json(r["plan_json"])) for r in rows]

    # ------------------------------------------------------------------
    def dna_from_prompt(self, prompt: str, images: list | None = None, rng: random.Random | None = None) -> BuildingDNA:
        """텍스트/이미지 요청을 DNA 로 변환 (AI 사용 가능 시)."""
        rng = rng or random.Random()
        if not self.ai.available():
            return random_dna(rng)
        from mcbuildai.generator.palette import PALETTES
        from .dna import TYPE_STYLES, WINDOW_PATTERNS
        data = self.ai.json(
            "너는 Minecraft 건축 설계자다. 요청을 주어진 선택지 안의 값으로만 분류한다.",
            f"요청: {prompt}\n선택지:\n btype={list(BUILDING_TYPES)}\n style={list(STYLES)}\n scale={list(SCALES)}\n"
            f" palette={list(PALETTES)}\n roof=['gable','hip','flat','dome','spire','pagoda','curved']\n"
            f" terrain={list(TERRAINS)}\n window_pattern={WINDOW_PATTERNS}\n symmetry=['symmetric','asymmetric']\n"
            '형식: {"btype":..,"style":..,"scale":..,"palette":..,"roof":..,"terrain":..,"window_pattern":..,"symmetry":..,"ornament":0-3}',
            images=images, max_tokens=500)
        forced = {k: v for k, v in data.items() if v is not None}
        dna = random_dna(rng, None, forced)
        for k in ("palette", "roof", "ornament"):
            if k in data:
                setattr(dna, k, data[k])
        dna.validate()
        return dna
