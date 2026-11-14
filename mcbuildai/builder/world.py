"""월드 준비: 건축 위치 자동 선정, 청크 고정, 지형 평탄화, 기초 공사, 지형 배치, 환경 바이옴."""
from __future__ import annotations

import math

from mcbuildai.config import Config
from mcbuildai.db import DB
from mcbuildai.generator.voxel import VoxelModel
from .rcon import Rcon

MAX_FILL = 32768


def plot_origin(cfg: Config, index: int) -> tuple[int, int, int]:
    """정사각 나선형으로 plot 위치를 배정한다 (건축물끼리 겹치지 않게)."""
    ox, oy, oz = cfg.get("build.origin")
    s = int(cfg.get("build.plot_spacing"))
    if index == 0:
        return ox, oy, oz
    k = math.ceil((math.sqrt(index + 1) - 1) / 2)
    t = 2 * k + 1
    m = t * t
    t -= 1
    i = index
    if i >= m - t:
        x, z = k - (m - i), -k
    elif i >= m - 2 * t:
        x, z = -k, -k + (m - t - i)
    elif i >= m - 3 * t:
        x, z = -k + (m - 2 * t - i), k
    else:
        x, z = k, k - (m - 3 * t - i)
    return ox + x * s, oy, oz + z * s


def next_plot(cfg: Config, db: DB) -> tuple[int, tuple[int, int, int]]:
    idx = int(db.kv_get("next_plot_index", 0))
    db.kv_set("next_plot_index", idx + 1)
    return idx, plot_origin(cfg, idx)


def fill_box(r: Rcon, x0, y0, z0, x1, y1, z1, block: str, mode: str = "") -> int:
    """fill 명령의 블록 수 제한(32768)에 맞춰 나눠 실행한다."""
    xa, xb = sorted((x0, x1)); ya, yb = sorted((y0, y1)); za, zb = sorted((z0, z1))
    cmds = []
    step = 32
    ystep = max(1, MAX_FILL // (step * step))
    for x in range(xa, xb + 1, step):
        for z in range(za, zb + 1, step):
            for y in range(ya, yb + 1, ystep):
                cmds.append(f"fill {x} {y} {z} {min(x + step - 1, xb)} {min(y + ystep - 1, yb)} {min(z + step - 1, zb)} "
                            f"{block}{(' ' + mode) if mode else ''}")
    for i in range(0, len(cmds), 50):
        r.commands(cmds[i:i + 50])
    return len(cmds)


def forceload(r: Rcon, x0, z0, x1, z1, add: bool = True) -> None:
    # forceload 는 한 번에 256 청크까지만 가능하므로 나눈다
    cx0, cx1 = sorted((x0 >> 4, x1 >> 4)); cz0, cz1 = sorted((z0 >> 4, z1 >> 4))
    for a in range(cx0, cx1 + 1, 16):
        for b in range(cz0, cz1 + 1, 16):
            r.command(f"forceload {'add' if add else 'remove'} {a * 16} {b * 16} "
                      f"{min(a + 15, cx1) * 16} {min(b + 15, cz1) * 16}")


def prepare_plot(r: Rcon, origin: tuple[int, int, int], model: VoxelModel, margin: int = 16,
                 height_clear: int | None = None) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """평탄화 + 기초: plot 영역을 비우고 평지(잔디/흙)로 되돌린다. 영역 (최소, 최대) 절대 좌표 반환."""
    (x0, y0, z0), (x1, y1, z1) = model.bounds()
    ox, oy, oz = origin
    ax0, az0 = ox + x0 - margin, oz + z0 - margin
    ax1, az1 = ox + x1 + margin, oz + z1 + margin
    top = oy + max(y1, 0) + (height_clear or 20)
    forceload(r, ax0, az0, ax1, az1)
    fill_box(r, ax0, oy, az0, ax1, min(top, 319), az1, "minecraft:air")
    fill_box(r, ax0, oy - 1, az0, ax1, oy - 1, az1, "minecraft:grass_block")
    fill_box(r, ax0, oy + min(y0, -1) - 4, az0, ax1, oy - 2, az1, "minecraft:dirt")
    r.command(f"kill @e[type=!player,x={ax0},y={oy - 10},z={az0},dx={ax1 - ax0},dy={top - oy + 10},dz={az1 - az0}]")
    return (ax0, oy - 1, az0), (ax1, top, az1)


def set_biome(r: Rcon, lo, hi, biome: str) -> None:
    """눈 내리는 장면 등을 위해 영역 바이옴 변경 (1.19.3+ fillbiome)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    step = 64
    for x in range(x0, x1 + 1, step):
        for z in range(z0, z1 + 1, step):
            r.command(f"fillbiome {x} {max(-64, y0 - 8)} {z} {min(x + step - 1, x1)} {min(319, y1)} {min(z + step - 1, z1)} {biome}")
