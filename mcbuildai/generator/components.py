"""건축 부품: 벽, 창문, 기둥, 바닥, 계단, 지붕(박공/우진각/평지붕/돔/첨탑/탑/한옥 곡선), 원형 탑."""
from __future__ import annotations

import math
import random

from .voxel import VoxelModel

FLOOR_H = 5  # 층고 (바닥 1 + 공간 4)


def stair(block: str, facing: str, half: str = "bottom") -> str:
    return f"{block}[facing={facing},half={half}]"


def slab(block: str, kind: str = "bottom") -> str:
    return f"{block}[type={kind}]"


def perimeter(m: VoxelModel, x0, z0, x1, z1, y0, y1, block, part, section) -> None:
    """사각형 둘레 벽만 채운다 (바닥/천장 제외)."""
    m.fill(x0, y0, z0, x1, y1, z0, block, part, section)
    m.fill(x0, y0, z1, x1, y1, z1, block, part, section)
    m.fill(x0, y0, z0, x0, y1, z1, block, part, section)
    m.fill(x1, y0, z0, x1, y1, z1, block, part, section)


# --------------------------------------------------------------------------
# 사각 매스
# --------------------------------------------------------------------------
def rect_mass(m: VoxelModel, P: dict, x0: int, z0: int, x1: int, z1: int, y0: int, floors: int,
              section: str, window_pattern: str, interior: bool, rng: random.Random,
              ornament: int = 1) -> int:
    """기초, 외벽, 모서리 기둥, 층 바닥, 창문, 내부를 만든다. 지붕 시작 높이를 반환."""
    top = y0 + floors * FLOOR_H
    m.fill(x0 - 1, y0 - 1, z0 - 1, x1 + 1, y0 - 1, z1 + 1, P["foundation"], "foundation", section)
    m.fill(x0, y0, z0, x1, y0, z1, P["floor"], "floor", section)
    perimeter(m, x0, z0, x1, z1, y0 + 1, top, P["wall"], "wall", section)
    # 모서리 기둥
    for x, z in ((x0, z0), (x0, z1), (x1, z0), (x1, z1)):
        m.fill(x, y0, z, x, top, z, P["pillar"], "pillar", section)
    # 층 구분 띠
    for f in range(1, floors + 1):
        y = y0 + f * FLOOR_H
        for x in range(x0, x1 + 1):
            for z in (z0, z1):
                m.set(x, y, z, P["accent"] if ornament else P["wall"], "wall", section)
        for z in range(z0, z1 + 1):
            for x in (x0, x1):
                m.set(x, y, z, P["accent"] if ornament else P["wall"], "wall", section)
        if f < floors:
            m.fill(x0 + 1, y, z0 + 1, x1 - 1, y, z1 - 1, P["floor"], "floor", section)
    # 중간 기둥
    if ornament >= 2:
        step = 4
        for x in range(x0 + step, x1, step):
            for z in (z0, z1):
                m.fill(x, y0, z, x, top, z, P["pillar"], "pillar", section)
        for z in range(z0 + step, z1, step):
            for x in (x0, x1):
                m.fill(x, y0, z, x, top, z, P["pillar"], "pillar", section)
    windows(m, P, x0, z0, x1, z1, y0, floors, window_pattern, section)
    # 출입구 (남쪽 중앙)
    cx = (x0 + x1) // 2
    for dy in (1, 2):
        m.remove(cx, y0 + dy, z1)
        if x1 - x0 >= 8:
            m.remove(cx - 1, y0 + dy, z1); m.remove(cx + 1, y0 + dy, z1)
    m.set(cx, y0 + 3, z1, P["trim"], "decor", section)
    if interior:
        interior_rooms(m, P, x0, z0, x1, z1, y0, floors, section, rng)
    return top


def windows(m: VoxelModel, P: dict, x0, z0, x1, z1, y0, floors, pattern, section) -> None:
    g = P["glass"]
    for f in range(floors):
        base = y0 + f * FLOOR_H
        if pattern == "band":
            rows = [base + 2, base + 3]
            for y in rows:
                for x in range(x0 + 1, x1):
                    for z in (z0, z1):
                        if m.get(x, y, z) and m.get(x, y, z).part == "wall":
                            m.set(x, y, z, g, "window", section)
                for z in range(z0 + 1, z1):
                    for x in (x0, x1):
                        if m.get(x, y, z) and m.get(x, y, z).part == "wall":
                            m.set(x, y, z, g, "window", section)
            continue
        spacing = {"grid": 3, "tall": 3, "sparse": 5, "arched": 4}.get(pattern, 3)
        heights = {"grid": (2, 3), "tall": (1, 2, 3, 4), "sparse": (2, 3), "arched": (2, 3, 4)}.get(pattern, (2, 3))
        if pattern == "tall" and f < floors - 1:
            heights = (2, 3, 4)
        for x in range(x0 + 2, x1 - 1, spacing):
            for z in (z0, z1):
                for h in heights:
                    if m.get(x, base + h, z) and m.get(x, base + h, z).part == "wall":
                        m.set(x, base + h, z, g, "window", section)
                if pattern == "arched":
                    m.set(x, base + max(heights) + 1, z, P["trim"], "decor", section)
        for z in range(z0 + 2, z1 - 1, spacing):
            for x in (x0, x1):
                for h in heights:
                    if m.get(x, base + h, z) and m.get(x, base + h, z).part == "wall":
                        m.set(x, base + h, z, g, "window", section)
                if pattern == "arched":
                    m.set(x, base + max(heights) + 1, z, P["trim"], "decor", section)


def interior_rooms(m: VoxelModel, P: dict, x0, z0, x1, z1, y0, floors, section, rng: random.Random) -> None:
    """층마다 칸막이, 계단, 조명을 배치한다."""
    w, d = x1 - x0, z1 - z0
    for f in range(floors):
        base = y0 + f * FLOOR_H
        # 칸막이: 넓은 층은 한쪽 벽으로 나눈다
        if w >= 12:
            px = x0 + w // 2 + rng.randint(-2, 2)
            for z in range(z0 + 1, z1):
                for y in range(base + 1, base + FLOOR_H):
                    m.set(px, y, z, P["wall"], "interior", section, overwrite=False)
            for y in (base + 1, base + 2):
                m.remove(px, y, (z0 + z1) // 2)
        # 조명
        for lx in range(x0 + 3, x1 - 2, 6):
            for lz in range(z0 + 3, z1 - 2, 6):
                if m.get(lx, base + 1, lz) is None:
                    m.set(lx, base + 1, lz, P["light"], "interior", section)
        # 계단 (북서쪽 모서리에서 위층으로)
        if f < floors - 1 and d >= 6:
            sx = x0 + 2
            # 위층 바닥에 계단 구멍 (머리 공간)
            for z in range(z0 + 1, min(z1, z0 + FLOOR_H)):
                m.remove(sx, base + FLOOR_H, z)
            for i in range(FLOOR_H):
                z = z0 + 1 + i
                if z >= z1:
                    break
                m.set(sx, base + 1 + i, z, stair(P["roof_stair"], "south"), "stair", section)


# --------------------------------------------------------------------------
# 지붕
# --------------------------------------------------------------------------
def roof_gable(m: VoxelModel, P: dict, x0, z0, x1, z1, y, section, overhang: int = 1) -> int:
    """박공지붕. 짧은 축 방향으로 경사."""
    along_x = (x1 - x0) >= (z1 - z0)
    if along_x:
        a0, a1 = z0 - overhang, z1 + overhang
        layers = (a1 - a0 + 1) // 2
        for i in range(layers):
            yy = y + i
            for x in range(x0 - overhang, x1 + overhang + 1):
                m.set(x, yy, a0 + i, stair(P["roof_stair"], "south"), "roof", section)
                m.set(x, yy, a1 - i, stair(P["roof_stair"], "north"), "roof", section)
            # 박공 벽
            for z in range(a0 + i + 1, a1 - i):
                if z0 <= z <= z1:
                    m.set(x0, yy, z, P["wall"], "wall", section)
                    m.set(x1, yy, z, P["wall"], "wall", section)
        if (a1 - a0 + 1) % 2 == 1:
            mid = (a0 + a1) // 2
            for x in range(x0 - overhang, x1 + overhang + 1):
                m.set(x, y + layers, mid, slab(P["roof_slab"]), "roof", section)
        return y + layers
    else:
        a0, a1 = x0 - overhang, x1 + overhang
        layers = (a1 - a0 + 1) // 2
        for i in range(layers):
            yy = y + i
            for z in range(z0 - overhang, z1 + overhang + 1):
                m.set(a0 + i, yy, z, stair(P["roof_stair"], "east"), "roof", section)
                m.set(a1 - i, yy, z, stair(P["roof_stair"], "west"), "roof", section)
            for x in range(a0 + i + 1, a1 - i):
                if x0 <= x <= x1:
                    m.set(x, yy, z0, P["wall"], "wall", section)
                    m.set(x, yy, z1, P["wall"], "wall", section)
        if (a1 - a0 + 1) % 2 == 1:
            mid = (a0 + a1) // 2
            for z in range(z0 - overhang, z1 + overhang + 1):
                m.set(mid, y + layers, z, slab(P["roof_slab"]), "roof", section)
        return y + layers


def roof_hip(m: VoxelModel, P: dict, x0, z0, x1, z1, y, section, overhang: int = 1,
             flare: bool = False, max_layers: int | None = None) -> int:
    """우진각(피라미드형) 지붕. flare=True 면 처마 모서리를 들어 올린다."""
    ax0, az0, ax1, az1 = x0 - overhang, z0 - overhang, x1 + overhang, z1 + overhang
    i = 0
    while ax0 + i <= ax1 - i and az0 + i <= az1 - i:
        if max_layers is not None and i >= max_layers:
            # 처마만 두르고 안쪽은 평평하게 덮는다 (다층 목탑용)
            m.fill(ax0 + i, y + i - 1, az0 + i, ax1 - i, y + i - 1, az1 - i, P["roof"], "roof", section, overwrite=False)
            break
        yy = y + i
        lx0, lx1, lz0, lz1 = ax0 + i, ax1 - i, az0 + i, az1 - i
        for x in range(lx0, lx1 + 1):
            m.set(x, yy, lz0, stair(P["roof_stair"], "south"), "roof", section)
            m.set(x, yy, lz1, stair(P["roof_stair"], "north"), "roof", section)
        for z in range(lz0 + 1, lz1):
            m.set(lx0, yy, z, stair(P["roof_stair"], "east"), "roof", section)
            m.set(lx1, yy, z, stair(P["roof_stair"], "west"), "roof", section)
        if lx1 - lx0 <= 1 or lz1 - lz0 <= 1:
            m.fill(lx0, yy, lz0, lx1, yy, lz1, P["roof"], "roof", section)
            i += 1
            break
        i += 1
    if flare:
        for cx, cz in ((ax0, az0), (ax0, az1), (ax1, az0), (ax1, az1)):
            m.set(cx, y + 1, cz, slab(P["roof_slab"]), "roof", section)
    return y + i


def roof_flat(m: VoxelModel, P: dict, x0, z0, x1, z1, y, section, parapet: bool = True,
              crenel: bool = False) -> int:
    m.fill(x0, y, z0, x1, y, z1, P["roof"], "roof", section)
    if parapet:
        for x in range(x0, x1 + 1):
            for z in (z0, z1):
                if not crenel or (x - x0) % 2 == 0:
                    m.set(x, y + 1, z, P["trim"], "roof", section)
        for z in range(z0, z1 + 1):
            for x in (x0, x1):
                if not crenel or (z - z0) % 2 == 0:
                    m.set(x, y + 1, z, P["trim"], "roof", section)
    return y + 1


def roof_dome(m: VoxelModel, P: dict, cx, cz, r, y, section, top_light: bool = True) -> int:
    for dx in range(-r, r + 1):
        for dz in range(-r, r + 1):
            for dy in range(0, r + 1):
                d = math.sqrt(dx * dx + dy * dy + dz * dz)
                if r - 1 < d <= r:
                    m.set(cx + dx, y + dy, cz + dz, P["roof"], "roof", section)
    if top_light:
        m.set(cx, y + r + 1, cz, P["light"], "decor", section)
    return y + r + 1


def roof_spire(m: VoxelModel, P: dict, cx, cz, r, y, section, height: int | None = None) -> int:
    height = height or max(4, int(r * 2.2))
    for dy in range(height):
        rr = r * (1 - dy / height)
        for dx in range(-r - 1, r + 2):
            for dz in range(-r - 1, r + 2):
                d = math.sqrt(dx * dx + dz * dz)
                if rr - 1 < d <= rr + 0.2:
                    m.set(cx + dx, y + dy, cz + dz, P["roof"], "roof", section)
    m.fill(cx, y + height, cz, cx, y + height + 2, cz, P["accent"], "decor", section)
    return y + height + 3


def roof_pagoda(m: VoxelModel, P: dict, x0, z0, x1, z1, y, section) -> int:
    """넓게 뻗은 처마 + 들린 모서리 (동아시아 목탑 지붕)."""
    return roof_hip(m, P, x0, z0, x1, z1, y, section, overhang=2, flare=True)


def roof_curved(m: VoxelModel, P: dict, x0, z0, x1, z1, y, section) -> int:
    """한옥 팔작지붕 근사: 우진각 위에 박공 + 처마 끝 들림."""
    top = roof_hip(m, P, x0, z0, x1, z1, y, section, overhang=2, flare=True)
    cx0, cx1 = x0 + (x1 - x0) // 3, x1 - (x1 - x0) // 3
    if cx1 - cx0 >= 2:
        for x in range(cx0, cx1 + 1):
            m.set(x, top, (z0 + z1) // 2, P["roof"], "roof", section)
        m.set(cx0, top + 1, (z0 + z1) // 2, P["accent"], "decor", section)
        m.set(cx1, top + 1, (z0 + z1) // 2, P["accent"], "decor", section)
    return top + 1


def apply_roof(m: VoxelModel, P: dict, roof: str, x0, z0, x1, z1, y, section, rng) -> int:
    cx, cz = (x0 + x1) // 2, (z0 + z1) // 2
    r = max(2, min(x1 - x0, z1 - z0) // 2)
    if roof == "gable":
        return roof_gable(m, P, x0, z0, x1, z1, y, section)
    if roof == "hip":
        return roof_hip(m, P, x0, z0, x1, z1, y, section)
    if roof == "flat":
        return roof_flat(m, P, x0, z0, x1, z1, y, section)
    if roof == "dome":
        roof_flat(m, P, x0, z0, x1, z1, y, section, parapet=False)
        return roof_dome(m, P, cx, cz, r, y + 1, section)
    if roof == "spire":
        roof_flat(m, P, x0, z0, x1, z1, y, section, parapet=False)
        return roof_spire(m, P, cx, cz, r + 1, y + 1, section)
    if roof == "pagoda":
        return roof_pagoda(m, P, x0, z0, x1, z1, y, section)
    if roof == "curved":
        return roof_curved(m, P, x0, z0, x1, z1, y, section)
    return roof_flat(m, P, x0, z0, x1, z1, y, section)


# --------------------------------------------------------------------------
# 원형 탑, 성벽, 열주
# --------------------------------------------------------------------------
def round_tower(m: VoxelModel, P: dict, cx, cz, r, y0, y1, section, windows_every: int = 4,
                taper: float = 0.0, stairs: bool = True) -> int:
    for y in range(y0, y1 + 1):
        rr = r - taper * (y - y0)
        for dx in range(-r - 1, r + 2):
            for dz in range(-r - 1, r + 2):
                d = math.sqrt(dx * dx + dz * dz)
                if rr - 1 < d <= rr:
                    blk = P["wall"]
                    part = "wall"
                    if windows_every and (y - y0) % windows_every in (2, 3) and (dx == 0 or dz == 0):
                        blk, part = P["glass"], "window"
                    m.set(cx + dx, y, cz + dz, blk, part, section)
                elif d <= rr - 1 and y == y0:
                    m.set(cx + dx, y, cz + dz, P["floor"], "floor", section)
    m.fill(cx - r - 1, y0 - 1, cz - r - 1, cx + r + 1, y0 - 1, cz + r + 1, P["foundation"], "foundation", section)
    if stairs and r >= 3:
        # 나선 계단
        steps = y1 - y0
        for i in range(steps):
            a = i * (math.pi / 6)
            sx = cx + int(round((r - 2) * math.cos(a)))
            sz = cz + int(round((r - 2) * math.sin(a)))
            m.set(sx, y0 + 1 + i, sz, P["accent"], "stair", section, overwrite=False)
    return y1 + 1


def crenellated_wall(m: VoxelModel, P: dict, x0, z0, x1, z1, y0, height, section) -> None:
    top = y0 + height
    for (ax, az, bx, bz) in ((x0, z0, x1, z0), (x0, z1, x1, z1), (x0, z0, x0, z1), (x1, z0, x1, z1)):
        m.fill(ax, y0 - 1, az, bx, y0 - 1, bz, P["foundation"], "foundation", section)
        m.fill(ax, y0, az, bx, top, bz, P["wall"], "wall", section)
    for x in range(x0, x1 + 1, 2):
        m.set(x, top + 1, z0, P["trim"], "roof", section)
        m.set(x, top + 1, z1, P["trim"], "roof", section)
    for z in range(z0, z1 + 1, 2):
        m.set(x0, top + 1, z, P["trim"], "roof", section)
        m.set(x1, top + 1, z, P["trim"], "roof", section)
    # 성문
    cx = (x0 + x1) // 2
    for dx in (-1, 0, 1):
        for dy in range(0, 4):
            m.remove(cx + dx, y0 + dy, z1)


def colonnade(m: VoxelModel, P: dict, x0, z0, x1, z1, y0, height, section, spacing: int = 3) -> None:
    for x in range(x0, x1 + 1, spacing):
        for z in (z0, z1):
            m.fill(x, y0, z, x, y0 + height, z, P["pillar"], "pillar", section)
            m.set(x, y0 + height, z, P["trim"], "decor", section)
    for z in range(z0, z1 + 1, spacing):
        for x in (x0, x1):
            m.fill(x, y0, z, x, y0 + height, z, P["pillar"], "pillar", section)
            m.set(x, y0 + height, z, P["trim"], "decor", section)
    m.fill(x0, y0 + height + 1, z0, x1, y0 + height + 1, z1, P["trim"], "wall", section, hollow=True)
