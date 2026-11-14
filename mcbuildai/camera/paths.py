"""카메라 경로 자동 생성.

좌표는 월드 절대 좌표. Minecraft 회전: yaw 0=남(+z), 90=서(-x), 180=북, -90=동(+x). pitch 양수=아래.
각 경로는 f(t, progress) -> (x, y, z, yaw, pitch) 함수이며 t 는 구간 내 경과 비율(0~1),
progress 는 건축 진행률(0~1, 건축 중 추적 카메라에서만 사용)이다.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable

from mcbuildai.db import DB

PathFn = Callable[[float, float], tuple[float, float, float, float, float]]

CAMERA_STYLES = {
    "orbit_360": "360도 회전",
    "aerial": "항공 촬영",
    "front": "정면 고정",
    "side": "측면 고정",
    "tracking": "건축 과정 추적",
    "stage_cuts": "건축 단계별 카메라 전환",
    "crane": "크레인 상승",
    "dolly_zoom": "자동 줌인/줌아웃",
    "handheld": "흔들림 효과",
}


def look_at(cx, cy, cz, tx, ty, tz) -> tuple[float, float]:
    dx, dy, dz = tx - cx, ty - cy, tz - cz
    yaw = math.degrees(math.atan2(-dx, dz))
    pitch = math.degrees(math.atan2(-dy, math.hypot(dx, dz)))
    return yaw, pitch


def ease(t: float) -> float:
    return t * t * (3 - 2 * t)


@dataclass
class Frame:
    """건축물 프레이밍 정보 (절대 좌표)."""
    cx: float
    cz: float
    base_y: float
    height: float
    radius: float     # 수평 반경

    @classmethod
    def from_bounds(cls, origin, lo, hi) -> "Frame":
        ox, oy, oz = origin
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        return cls(cx=ox + (x0 + x1) / 2 + 0.5, cz=oz + (z0 + z1) / 2 + 0.5, base_y=oy + max(0, y0),
                   height=max(4, y1 - max(0, y0) + 1), radius=max(6, math.hypot(x1 - x0, z1 - z0) / 2))

    @property
    def dist(self) -> float:
        # 세로 화면(9:16)에서도 잘리지 않도록 높이와 폭 중 큰 쪽 기준
        return max(self.radius * 1.9, self.height * 1.5) + 6


def _with_shake(fn: PathFn, amp: float, seed: int) -> PathFn:
    rng = random.Random(seed)
    phases = [rng.random() * 10 for _ in range(5)]

    def g(t, p):
        x, y, z, yaw, pitch = fn(t, p)
        k = t * 40
        return (x + amp * 0.15 * math.sin(k * 1.3 + phases[0]), y + amp * 0.15 * math.sin(k * 1.7 + phases[1]),
                z + amp * 0.15 * math.sin(k * 1.1 + phases[2]), yaw + amp * math.sin(k * 2.1 + phases[3]),
                pitch + amp * 0.6 * math.sin(k * 1.9 + phases[4]))
    return g


def hero(f: Frame, angle_deg: float = 45, elev: float = 0.45) -> PathFn:
    """고정 대표 구도 (Before & After 에 같은 구도를 쓴다)."""
    a = math.radians(angle_deg)
    d = f.dist
    x, z = f.cx + math.sin(a) * d, f.cz + math.cos(a) * d
    y = f.base_y + f.height * elev + d * 0.25
    yaw, pitch = look_at(x, y, z, f.cx, f.base_y + f.height * 0.45, f.cz)
    return lambda t, p: (x, y, z, yaw, pitch)


def orbit(f: Frame, start_deg: float = 0, turns: float = 1.0, elev: float = 0.5, dist_mult: float = 1.0) -> PathFn:
    def fn(t, p):
        a = math.radians(start_deg) + 2 * math.pi * turns * t
        d = f.dist * dist_mult
        x, z = f.cx + math.sin(a) * d, f.cz + math.cos(a) * d
        y = f.base_y + f.height * elev + d * 0.2
        yaw, pitch = look_at(x, y, z, f.cx, f.base_y + f.height * 0.45, f.cz)
        return x, y, z, yaw, pitch
    return fn


def aerial(f: Frame) -> PathFn:
    def fn(t, p):
        a = math.radians(-60 + 120 * ease(t))
        d = f.dist * 1.1
        x, z = f.cx + math.sin(a) * d * 0.7, f.cz + math.cos(a) * d * 0.7
        y = f.base_y + f.height + d * 0.9
        yaw, pitch = look_at(x, y, z, f.cx, f.base_y + f.height * 0.3, f.cz)
        return x, y, z, yaw, pitch
    return fn


def front(f: Frame) -> PathFn:
    return hero(f, angle_deg=0, elev=0.4)


def side(f: Frame) -> PathFn:
    return hero(f, angle_deg=90, elev=0.4)


def tracking(f: Frame) -> PathFn:
    """건축 진행 높이를 따라 시선과 카메라가 올라가며 천천히 돈다."""
    def fn(t, p):
        a = math.radians(30 + 90 * t)
        d = f.dist * 0.9
        h = f.base_y + f.height * max(0.15, p)
        x, z = f.cx + math.sin(a) * d, f.cz + math.cos(a) * d
        y = h + d * 0.3
        yaw, pitch = look_at(x, y, z, f.cx, h - 2, f.cz)
        return x, y, z, yaw, pitch
    return fn


def stage_cuts(f: Frame) -> PathFn:
    angles = [30, 120, 210, 300]

    def fn(t, p):
        stage = min(3, int(p * 4))
        return hero(f, angles[stage], 0.35 + 0.1 * stage)(t, p)
    return fn


def crane(f: Frame) -> PathFn:
    def fn(t, p):
        a = math.radians(20 + 40 * t)
        d = f.dist
        x, z = f.cx + math.sin(a) * d, f.cz + math.cos(a) * d
        y = f.base_y + 2 + (f.height + d * 0.5) * ease(t)
        yaw, pitch = look_at(x, y, z, f.cx, f.base_y + f.height * 0.5, f.cz)
        return x, y, z, yaw, pitch
    return fn


def dolly_zoom(f: Frame) -> PathFn:
    def fn(t, p):
        k = 1.4 - 0.7 * ease(t)  # 멀리서 가까이
        return orbit(f, 35, 0.0, 0.45, k)(0, p)
    return fn


def interior(f: Frame) -> PathFn:
    """1층 높이에서 건물 중심을 가로지른다."""
    def fn(t, p):
        y = f.base_y + 2.6
        x = f.cx
        z = f.cz + f.radius * 0.5 - f.radius * 1.0 * ease(t)
        yaw = 180 + 25 * math.sin(t * math.pi * 2)
        return x, y, z, yaw, 5.0
    return fn


BUILDERS = {"orbit_360": lambda f: orbit(f, 30, 0.6), "aerial": aerial, "front": front, "side": side,
            "tracking": tracking, "stage_cuts": stage_cuts, "crane": crane, "dolly_zoom": dolly_zoom,
            "handheld": lambda f: _with_shake(hero(f, 40), 0.8, 1)}


def build_path(style: str, f: Frame, shake: bool = False, seed: int = 0) -> PathFn:
    fn = BUILDERS[style](f)
    return _with_shake(fn, 0.6, seed) if shake and style != "handheld" else fn


def choose_camera_style(db: DB, rng: random.Random, btype: str, avoid: list[str] | None = None) -> str:
    """영상별 카메라 스타일 변경: 최근 사용 스타일을 피하고 성과 가중치 반영."""
    from mcbuildai.analysis.performance import category_weights
    w = category_weights(db).get("camera_style", {})
    recent = [r["camera_style"] for r in db.query(
        "SELECT camera_style FROM buildings WHERE camera_style IS NOT NULL ORDER BY id DESC LIMIT 3")]
    recent = (avoid or []) + recent
    opts = [s for s in CAMERA_STYLES if s not in recent[:2]]
    if btype in ("skyscraper", "lighthouse", "tower"):
        opts = [s for s in opts if s != "side"] or opts
    return rng.choices(opts, weights=[max(0.05, w.get(s, 1.0)) for s in opts], k=1)[0]
