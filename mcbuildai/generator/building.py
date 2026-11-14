"""DNA로부터 블록 모델을 만드는 절차적 생성기 (genMC 연결이 없을 때의 예비 생성기).

모든 건축물은 x, z 중심이 0 이 되도록 배치된다.
"""
from __future__ import annotations

import math
import random

from mcbuildai.planner.dna import BuildingDNA, STYLES
from . import components as C
from .palette import PALETTES
from .terrain import build_terrain
from .voxel import VoxelModel


def _palette(dna: BuildingDNA) -> dict:
    P = dict(PALETTES[dna.palette])
    if dna.secondary_style:
        # 양식 혼합: 보조 양식의 팔레트에서 지붕/장식 재료를 가져온다
        sec = PALETTES[STYLES[dna.secondary_style]["palettes"][0]]
        for k in ("roof", "roof_stair", "roof_slab", "accent"):
            P[k] = sec[k]
    return P


def _half(n: int) -> int:
    return n // 2


def gen_house(m, P, dna, rng):
    w, d = _half(dna.width), _half(dna.depth)
    top = C.rect_mass(m, P, -w, -d, w, d, 0, dna.floors, "main", dna.window_pattern, dna.interior, rng, dna.ornament)
    C.apply_roof(m, P, dna.roof, -w, -d, w, d, top, "main", rng)
    if dna.symmetry == "asymmetric":
        ww = max(3, w // 2)
        x0 = w
        t2 = C.rect_mass(m, P, x0, -d + 1, x0 + ww * 2, d - 2, 0, max(1, dna.floors - 1), "wing",
                         dna.window_pattern, dna.interior, rng, dna.ornament)
        C.apply_roof(m, P, "gable" if dna.roof not in ("flat",) else "flat", x0, -d + 1, x0 + ww * 2, d - 2, t2, "wing", rng)
    # 굴뚝
    if dna.style in ("medieval", "nordic", "steampunk"):
        m.fill(-w + 2, 1, -d + 2, -w + 2, top + max(4, d) , -d + 2, P["trim"], "decor", "main")


def gen_mansion(m, P, dna, rng):
    w, d = _half(dna.width), _half(dna.depth)
    cw = max(4, w // 3)
    top = C.rect_mass(m, P, -cw, -d, cw, d, 0, dna.floors + 1, "center", dna.window_pattern, dna.interior, rng, dna.ornament)
    C.apply_roof(m, P, dna.roof, -cw, -d, cw, d, top, "center", rng)
    wd = max(3, d - 3)
    for side, (a, b) in (("west_wing", (-w, -cw)), ("east_wing", (cw, w))):
        if dna.symmetry == "asymmetric" and side == "east_wing":
            wd2 = max(3, wd - 2)
            t = C.rect_mass(m, P, a, -wd2, b, wd2, 0, max(1, dna.floors - 1), side, dna.window_pattern, dna.interior, rng, dna.ornament)
            C.apply_roof(m, P, "hip", a, -wd2, b, wd2, t, side, rng)
            continue
        t = C.rect_mass(m, P, a, -wd, b, wd, 0, dna.floors, side, dna.window_pattern, dna.interior, rng, dna.ornament)
        C.apply_roof(m, P, dna.roof if dna.roof != "spire" else "hip", a, -wd, b, wd, t, side, rng)
    # 정원 앞 계단
    for i in range(3):
        m.fill(-2, -1 + 0, d + 1 + i, 2, -1, d + 1 + i, P["foundation"], "foundation", "center")


def gen_tower(m, P, dna, rng):
    r = max(3, _half(dna.width) // 2 + 2)
    h = dna.floors * C.FLOOR_H + rng.randint(4, 10)
    if dna.symmetry == "symmetric" or dna.style in ("futuristic",):
        top = C.round_tower(m, P, 0, 0, r, 0, h, "tower", taper=0.0)
        if dna.roof == "dome":
            C.roof_dome(m, P, 0, 0, r, top, "tower")
        elif dna.roof == "flat":
            C.roof_flat(m, P, -r, -r, r, r, top, "tower", crenel=True)
        else:
            C.roof_spire(m, P, 0, 0, r + 1, top, "tower")
    else:
        top = C.rect_mass(m, P, -r, -r, r, r, 0, dna.floors + 1, "tower", dna.window_pattern, dna.interior, rng, dna.ornament)
        C.apply_roof(m, P, dna.roof, -r, -r, r, r, top, "tower", rng)
        # 부속 작은 탑
        rr = max(2, r // 2)
        t2 = C.round_tower(m, P, r + rr, -r, rr, 0, top - 4, "turret", stairs=False)
        C.roof_spire(m, P, r + rr, -r, rr + 1, t2, "turret")


def gen_castle(m, P, dna, rng):
    w, d = _half(dna.width), _half(dna.depth)
    wall_h = 8 if dna.scale == "large" else 12
    C.crenellated_wall(m, P, -w, -d, w, d, 0, wall_h, "curtain_wall")
    tr = 3 if dna.scale == "large" else 5
    for i, (cx, cz) in enumerate(((-w, -d), (w, -d), (-w, d), (w, d))):
        sec = f"tower_{i + 1}"
        top = C.round_tower(m, P, cx, cz, tr, 0, wall_h + 6, sec)
        if dna.roof == "spire" or dna.style in ("fantasy", "gothic"):
            C.roof_spire(m, P, cx, cz, tr + 1, top, sec)
        else:
            C.roof_flat(m, P, cx - tr, cz - tr, cx + tr, cz + tr, top, sec, crenel=True)
    kw, kd = max(5, w // 3), max(5, d // 3)
    floors = dna.floors + 1
    top = C.rect_mass(m, P, -kw, -kd, kw, kd, 0, floors, "keep", dna.window_pattern, dna.interior, rng, dna.ornament)
    if dna.roof in ("gable", "hip"):
        C.apply_roof(m, P, dna.roof, -kw, -kd, kw, kd, top, "keep", rng)
    else:
        C.roof_flat(m, P, -kw, -kd, kw, kd, top, "keep", crenel=True)
        tt = C.round_tower(m, P, kw, -kd, 3, top, top + 8, "keep_tower", stairs=False)
        C.roof_spire(m, P, kw, -kd, 4, tt, "keep_tower")


def gen_temple(m, P, dna, rng):
    w, d = _half(dna.width), _half(dna.depth)
    # 계단식 기단
    for i in range(3):
        m.fill(-w - 3 + i, i, -d - 3 + i, w + 3 - i, i, d + 3 - i, P["foundation"], "foundation", "podium")
    base = 3
    col_h = 6 + dna.floors * 2
    C.colonnade(m, P, -w, -d, w, d, base, col_h, "colonnade", spacing=3 if dna.scale != "huge" else 4)
    cw, cd = max(3, w - 4), max(3, d - 4)
    C.perimeter(m, -cw, -cd, cw, cd, base, base + col_h, P["wall"], "wall", "cella")
    for dy in range(4):
        for dx in (-1, 0, 1):
            m.remove(dx, base + dy, cd)
    roof_y = base + col_h + 2
    if dna.roof == "dome":
        C.roof_flat(m, P, -w, -d, w, d, roof_y - 1, "roof", parapet=False)
        C.roof_dome(m, P, 0, 0, max(3, min(w, d) - 1), roof_y, "roof")
    elif dna.roof in ("gable", "pagoda", "hip"):
        C.apply_roof(m, P, dna.roof, -w, -d, w, d, roof_y - 1, "roof", rng)
    else:
        C.roof_flat(m, P, -w, -d, w, d, roof_y - 1, "roof")
    m.set(0, base + 1, 0, P["light"], "interior", "cella")
    m.fill(-1, base, -cd + 2, 1, base + 1, -cd + 3, P["accent"], "interior", "cella")


def gen_cathedral(m, P, dna, rng):
    w, d = _half(dna.width), _half(dna.depth)
    nw = max(4, w // 3)
    floors = dna.floors + 2
    top = C.rect_mass(m, P, -nw, -d, nw, d, 0, floors, "nave", "tall", dna.interior, rng, max(2, dna.ornament))
    C.roof_gable(m, P, -nw, -d, nw, d, top, "nave")
    tw = max(3, nw // 2 + 1)
    tz0 = -max(3, d // 3)
    top2 = C.rect_mass(m, P, -w, tz0, w, tz0 + 2 * tw, 0, floors - 1, "transept", "tall", False, rng, dna.ornament)
    C.roof_gable(m, P, -w, tz0, w, tz0 + 2 * tw, top2, "transept")
    # 앞쪽 두 첨탑
    tr = max(2, tw - 1)
    for i, cx in enumerate((-nw, nw)):
        sec = f"spire_{i + 1}"
        t = C.round_tower(m, P, cx, d, tr, 0, top + 8, sec, stairs=False)
        C.roof_spire(m, P, cx, d, tr + 1, t, sec, height=tr * 4 + 6)
    # 후진(apse)
    C.round_tower(m, P, 0, -d, nw, 0, top - 2, "apse", stairs=False, windows_every=5)
    C.roof_dome(m, P, 0, -d, nw, top - 1, "apse", top_light=False)
    # 장미창
    for a in range(0, 360, 30):
        rx = int(round(math.cos(math.radians(a)) * 2))
        ry = int(round(math.sin(math.radians(a)) * 2))
        m.set(rx, top - 3 + ry, d, P["glass"], "window", "nave")


def gen_skyscraper(m, P, dna, rng):
    w, d = _half(dna.width), _half(dna.depth)
    total_floors = dna.floors * 4 + rng.randint(2, 8)
    y = 0
    tiers = 3 if dna.scale in ("large", "huge") else 2
    per = max(2, total_floors // tiers)
    for t in range(tiers):
        sec = f"tier_{t + 1}"
        floors = per if t < tiers - 1 else total_floors - per * (tiers - 1)
        top = C.rect_mass(m, P, -w, -d, w, d, y, floors, sec, "band" if dna.window_pattern in ("band", "grid") else dna.window_pattern,
                          dna.interior and t == 0, rng, dna.ornament)
        C.roof_flat(m, P, -w, -d, w, d, top, sec)
        y = top + 1
        shrink = max(2, w // 4)
        w, d = max(3, w - shrink), max(3, d - shrink)
    if dna.roof == "spire":
        C.roof_spire(m, P, 0, 0, max(2, w), y, "crown", height=w * 4)
    elif dna.roof == "dome":
        C.roof_dome(m, P, 0, 0, max(2, min(w, d)), y, "crown")
    m.fill(0, y, 0, 0, y + 12, 0, "minecraft:iron_block", "decor", "crown")
    m.set(0, y + 13, 0, P["light"], "decor", "crown")


def gen_pagoda(m, P, dna, rng):
    tiers = max(3, dna.floors + 2)
    w = min(_half(dna.width), 4 + tiers * 2)
    y = 0
    m.fill(-w - 2, -1, -w - 2, w + 2, -1, w + 2, P["foundation"], "foundation", "base")
    for t in range(tiers):
        sec = f"tier_{t + 1}"
        hw = max(2, w - t * 2)
        C.perimeter(m, -hw, -hw, hw, hw, y, y + 3, P["wall"], "wall", sec)
        for x, z in ((-hw, -hw), (-hw, hw), (hw, -hw), (hw, hw)):
            m.fill(x, y, z, x, y + 3, z, P["pillar"], "pillar", sec)
        for dy in (1, 2):
            m.set(0, y + dy, hw, P["glass"], "window", sec)
            m.set(0, y + dy, -hw, P["glass"], "window", sec)
            m.set(hw, y + dy, 0, P["glass"], "window", sec)
            m.set(-hw, y + dy, 0, P["glass"], "window", sec)
        m.fill(-hw + 1, y, -hw + 1, hw - 1, y, hw - 1, P["floor"], "floor", sec)
        last = t == tiers - 1
        C.roof_hip(m, P, -hw, -hw, hw, hw, y + 4, sec, overhang=2, flare=True,
                   max_layers=None if last else 2)
        y += 6
    m.fill(0, y - 2, 0, 0, y + 6, 0, P["accent"], "decor", "finial")


def gen_lighthouse(m, P, dna, rng):
    r = max(3, _half(dna.width) // 2 + 1)
    h = 18 + dna.floors * 5
    top = C.round_tower(m, P, 0, 0, r, 0, h, "tower", taper=0.04, windows_every=6)
    # 전망대 테라스
    for dx in range(-r - 2, r + 3):
        for dz in range(-r - 2, r + 3):
            dd = math.sqrt(dx * dx + dz * dz)
            if dd <= r + 2:
                m.set(dx, top, dz, P["trim"], "floor", "gallery")
            if r + 1 < dd <= r + 2:
                m.set(dx, top + 1, dz, P["detail"], "decor", "gallery")
    lr = max(2, r - 1)
    for dy in range(1, 5):
        for dx in range(-lr, lr + 1):
            for dz in range(-lr, lr + 1):
                dd = math.sqrt(dx * dx + dz * dz)
                if lr - 1 < dd <= lr:
                    m.set(dx, top + dy, dz, P["glass"], "window", "lantern_room")
    m.set(0, top + 1, 0, "minecraft:glowstone", "decor", "lantern_room")
    m.set(0, top + 2, 0, "minecraft:sea_lantern", "decor", "lantern_room")
    if dna.roof == "spire":
        C.roof_spire(m, P, 0, 0, lr + 1, top + 5, "cap")
    else:
        C.roof_dome(m, P, 0, 0, lr + 1, top + 5, "cap")
    # 관리인 오두막
    t = C.rect_mass(m, P, r + 1, -3, r + 9, 3, 0, 1, "keeper_house", "grid", dna.interior, rng, 1)
    C.roof_gable(m, P, r + 1, -3, r + 9, 3, t, "keeper_house")


def gen_ziggurat(m, P, dna, rng):
    w = _half(dna.width)
    y = 0
    level = 0
    while w > 4:
        sec = f"level_{level + 1}"
        m.fill(-w, y, -w, w, y + 3, w, P["wall"], "wall", sec, hollow=True)
        m.fill(-w, y + 3, -w, w, y + 3, w, P["trim"], "floor", sec)
        # 중앙 계단
        for i in range(4):
            m.fill(-2, y + i, w - i, 2, y + i, w - i, C.stair(P["roof_stair"], "north"), "stair", sec)
        w -= 4
        y += 4
        level += 1
    t = C.rect_mass(m, P, -w, -w, w, w, y, 1, "shrine", "sparse", False, rng, dna.ornament)
    C.roof_flat(m, P, -w, -w, w, w, t, "shrine")
    m.set(0, t + 1, 0, P["light"], "decor", "shrine")


def gen_hanok(m, P, dna, rng):
    """ㄷ자 한옥: 안채 + 좌우 날개채, 가운데 마당."""
    w, d = _half(dna.width), _half(dna.depth)
    bw = max(3, d // 3)
    # 기단
    m.fill(-w - 2, -1, -d - 2, w + 2, -1, d + 2, P["foundation"], "foundation", "base")
    t = C.rect_mass(m, P, -w, -d, w, -d + 2 * bw, 0, 1, "anchae", "sparse", dna.interior, rng, 2)
    C.roof_curved(m, P, -w, -d, w, -d + 2 * bw, t, "anchae")
    for side, (a, b) in (("west_wing", (-w, -w + 2 * bw)), ("east_wing", (w - 2 * bw, w))):
        tt = C.rect_mass(m, P, a, -d + 2 * bw + 1, b, d, 0, 1, side, "sparse", dna.interior, rng, 2)
        C.roof_curved(m, P, a, -d + 2 * bw + 1, b, d, tt, side)
    # 마당과 담장
    m.fill(-w + 2 * bw + 1, -1, -d + 2 * bw + 1, w - 2 * bw - 1, -1, d, "minecraft:dirt_path", "landscape", "yard")
    for x in range(-w - 4, w + 5):
        m.set(x, 0, d + 4, P["detail"], "decor", "fence")
    for dx in (-1, 0, 1):
        m.remove(dx, 0, d + 4)


def gen_citadel(m, P, dna, rng):
    """초대형 요새: 이중 성벽, 다수 탑, 중앙 거대 첨탑."""
    w, d = _half(dna.width), _half(dna.depth)
    C.crenellated_wall(m, P, -w, -d, w, d, 0, 14, "outer_wall")
    iw, idd = int(w * 0.6), int(d * 0.6)
    C.crenellated_wall(m, P, -iw, -idd, iw, idd, 0, 20, "inner_wall")
    n = 8
    for i in range(n):
        a = 2 * math.pi * i / n
        cx, cz = int(math.cos(a) * w * 0.98), int(math.sin(a) * d * 0.98)
        cx = max(-w, min(w, cx)); cz = max(-d, min(d, cz))
        sec = f"outer_tower_{i + 1}"
        top = C.round_tower(m, P, cx, cz, 5, 0, 22, sec, stairs=False)
        C.roof_spire(m, P, cx, cz, 6, top, sec)
    for i, (cx, cz) in enumerate(((-iw, -idd), (iw, -idd), (-iw, idd), (iw, idd))):
        sec = f"inner_tower_{i + 1}"
        top = C.round_tower(m, P, cx, cz, 6, 0, 30, sec, stairs=False)
        C.roof_spire(m, P, cx, cz, 7, top, sec)
    kr = max(8, min(iw, idd) // 2)
    top = C.round_tower(m, P, 0, 0, kr, 0, 50 + dna.floors * 3, "central_spire", taper=0.05)
    C.roof_spire(m, P, 0, 0, kr, top, "central_spire", height=kr * 4)


GENERATORS = {
    "house": gen_house, "mansion": gen_mansion, "tower": gen_tower, "castle": gen_castle,
    "temple": gen_temple, "cathedral": gen_cathedral, "skyscraper": gen_skyscraper,
    "pagoda": gen_pagoda, "lighthouse": gen_lighthouse, "ziggurat": gen_ziggurat,
    "hanok": gen_hanok, "citadel": gen_citadel,
}


def generate(dna: BuildingDNA, with_terrain: bool = True) -> VoxelModel:
    dna.resolve_dimensions()
    rng = random.Random(dna.seed)
    P = _palette(dna)
    building = VoxelModel()
    GENERATORS[dna.btype](building, P, dna, rng)
    if not with_terrain:
        return building
    model = build_terrain(building, dna, rng)
    model.merge(building)
    return model
