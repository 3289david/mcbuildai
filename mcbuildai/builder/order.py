"""건축 순서 생성과 최적화.

각 전략은 (좌표, Voxel) 목록을 배치 순서대로 돌려준다. 결과는 '단계(group)' 경계 정보를 포함해
버스트형 연출(구역별/재료별/순차 등장)에서 단계 사이에 잠깐 멈출 수 있게 한다.
"""
from __future__ import annotations

import math
import random

from mcbuildai.generator.palette import is_gravity
from mcbuildai.generator.voxel import PART_ORDER, Voxel, VoxelModel

Placement = tuple[tuple[int, int, int], Voxel]


def _support_fix(order: list[Placement]) -> list[Placement]:
    """중력 블록은 아래 블록이 먼저 놓이도록 뒤로 미룬다."""
    placed: set[tuple[int, int, int]] = set()
    out, deferred = [], []
    for item in order:
        (x, y, z), v = item
        if is_gravity(v.block) and (x, y - 1, z) not in placed and y > -1:
            deferred.append(item)
            continue
        out.append(item)
        placed.add((x, y, z))
    deferred.sort(key=lambda it: it[0][1])
    return out + deferred


def _nearest_neighbor_rows(items: list[Placement]) -> list[Placement]:
    """같은 층 안에서 지그재그(뱀) 순서: 이동 거리를 줄여 자연스럽게 보이게 한다."""
    rows: dict[int, list[Placement]] = {}
    for it in items:
        rows.setdefault(it[0][2], []).append(it)
    out = []
    for i, z in enumerate(sorted(rows)):
        row = sorted(rows[z], key=lambda it: it[0][0], reverse=bool(i % 2))
        out += row
    return out


def order_blocks(model: VoxelModel, strategy: str, seed: int = 0) -> tuple[list[Placement], list[int]]:
    """(배치 목록, 단계 경계 인덱스 목록)."""
    rng = random.Random(seed)
    items: list[Placement] = list(model.items())
    groups: list[int] = []
    if not items:
        return [], []
    (x0, y0, z0), (x1, y1, z1) = model.bounds()
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2

    if strategy == "bottom_up":
        items.sort(key=lambda it: (it[0][1], math.hypot(it[0][0] - cx, it[0][2] - cz)))
    elif strategy == "top_down":
        items.sort(key=lambda it: (-it[0][1], it[0][0], it[0][2]))
    elif strategy == "layer":
        out = []
        for y in sorted({it[0][1] for it in items}):
            groups.append(len(out))
            out += _nearest_neighbor_rows([it for it in items if it[0][1] == y])
        items = out
    elif strategy == "natural":
        # 층 순서를 유지하되 층 안에서는 국소적으로 섞어 사람이 짓는 듯한 느낌
        by_y: dict[int, list[Placement]] = {}
        for it in items:
            by_y.setdefault(it[0][1], []).append(it)
        out = []
        for y in sorted(by_y):
            row = _nearest_neighbor_rows(by_y[y])
            chunk = 12
            for i in range(0, len(row), chunk):
                seg = row[i:i + chunk]
                rng.shuffle(seg)
                out += seg
        items = out
    elif strategy == "wave":
        ang = rng.choice([0, math.pi / 4, math.pi / 2, 3 * math.pi / 4, math.pi])
        dx, dz = math.cos(ang), math.sin(ang)
        items.sort(key=lambda it: (round(it[0][0] * dx + it[0][2] * dz), it[0][1]))
    elif strategy == "multi_front":
        k = max(3, min(8, len(items) // 4000 + 3))
        seeds = [items[rng.randrange(len(items))][0] for _ in range(k)]
        def key(it):
            (x, y, z), _ = it
            d = min(math.dist((x, z), (s[0], s[2])) for s in seeds)
            return (y // 3, d)
        items.sort(key=key)
    elif strategy in ("section", "sequential"):
        secs: dict[str, list[Placement]] = {}
        for it in items:
            secs.setdefault(it[1].section, []).append(it)
        # 지형 -> 아래쪽 구역 -> 위쪽 구역 순
        def sec_key(name):
            ys = [it[0][1] for it in secs[name]]
            return (0 if name in ("ground", "water", "seabed", "surroundings", "lava", "pond", "mountain") else 1,
                    min(ys), name)
        out = []
        for name in sorted(secs, key=sec_key):
            if strategy == "sequential":
                parts: dict[str, list[Placement]] = {}
                for it in secs[name]:
                    parts.setdefault(it[1].part, []).append(it)
                for p in sorted(parts, key=lambda p: PART_ORDER.index(p) if p in PART_ORDER else 99):
                    groups.append(len(out))
                    out += sorted(parts[p], key=lambda it: (it[0][1], it[0][0], it[0][2]))
            else:
                groups.append(len(out))
                out += sorted(secs[name], key=lambda it: (it[0][1], it[0][2], it[0][0]))
        items = out
    elif strategy == "material":
        def pk(it):
            p = it[1].part
            return (PART_ORDER.index(p) if p in PART_ORDER else 99, it[0][1], it[0][2], it[0][0])
        items.sort(key=pk)
        last = None
        for i, it in enumerate(items):
            if it[1].part != last:
                groups.append(i)
                last = it[1].part
    else:
        raise ValueError(f"알 수 없는 건축 순서: {strategy}")
    return _support_fix(items), groups


def compress_runs(order: list[Placement], max_run: int = 64) -> list[tuple[str, tuple]]:
    """연속된 같은 블록(같은 y, z, x+1)을 fill 명령으로 묶는다. Fast Build 전용.

    반환: [('set', (x,y,z,block)) | ('fill', (x0,y,z,x1,block))]
    """
    out = []
    i = 0
    n = len(order)
    while i < n:
        (x, y, z), v = order[i]
        step = 0
        if i + 1 < n:
            (nx, ny, nz), nv = order[i + 1]
            if ny == y and nz == z and nv.block == v.block and abs(nx - x) == 1:
                step = nx - x
        j = i + 1
        if step:
            while (j < n and j - i < max_run and order[j][1].block == v.block
                   and order[j][0] == (x + step * (j - i), y, z)):
                j += 1
        if j - i >= 3:
            x_end = x + step * (j - i - 1)
            out.append(("fill", (min(x, x_end), y, z, max(x, x_end), v.block, j - i)))
        else:
            for k in range(i, j):
                (a, b, c), vv = order[k]
                out.append(("set", (a, b, c, vv.block, 1)))
        i = j
    return out
