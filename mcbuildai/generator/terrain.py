"""주변 지형과 조경 자동 설계. y=-1 이 기존 지면(평지 월드 잔디층)과 같은 높이다."""
from __future__ import annotations

import math
import random

from .voxel import VoxelModel

GROUND = {
    "plains": ("minecraft:grass_block", "minecraft:dirt"),
    "forest": ("minecraft:grass_block", "minecraft:dirt"),
    "island": ("minecraft:grass_block", "minecraft:dirt"),
    "mountain": ("minecraft:grass_block", "minecraft:stone"),
    "sea": ("minecraft:stone_bricks", "minecraft:stone"),
    "desert": ("minecraft:sandstone", "minecraft:sandstone"),
    "snow": ("minecraft:snow_block", "minecraft:dirt"),
    "lava": ("minecraft:blackstone", "minecraft:basalt"),
}


def _leaves(kind: str) -> str:
    return f"minecraft:{kind}_leaves[persistent=true]"


def tree(m: VoxelModel, x: int, z: int, base_y: int, kind: str, rng: random.Random) -> None:
    h = rng.randint(4, 7) if kind != "spruce" else rng.randint(6, 9)
    log = f"minecraft:{kind}_log"
    for y in range(base_y, base_y + h):
        m.set(x, y, z, log, "landscape", "trees", overwrite=False)
    top = base_y + h
    if kind == "spruce":
        for i in range(h):
            r = max(0, (h - i) // 2 - 0)
            y = base_y + 2 + i
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if abs(dx) + abs(dz) <= r + 1 and (dx or dz or y >= top):
                        m.set(x + dx, y, z + dz, _leaves("spruce"), "landscape", "trees", overwrite=False)
        m.set(x, top + 1, z, _leaves("spruce"), "landscape", "trees", overwrite=False)
        return
    r = 2 if kind != "cherry" else 3
    for dx in range(-r, r + 1):
        for dy in range(-2, 2):
            for dz in range(-r, r + 1):
                if dx * dx + (dy * 1.5) ** 2 + dz * dz <= r * r + 1:
                    m.set(x + dx, top + dy, z + dz, _leaves(kind), "landscape", "trees", overwrite=False)


def build_terrain(building: VoxelModel, dna, rng: random.Random) -> VoxelModel:
    (bx0, by0, bz0), (bx1, by1, bz1) = building.bounds()
    margin = max(10, int(max(bx1 - bx0, bz1 - bz0) * 0.45))
    x0, x1, z0, z1 = bx0 - margin, bx1 + margin, bz0 - margin, bz1 + margin
    cx, cz = (bx0 + bx1) / 2, (bz0 + bz1) / 2
    rx, rz = (x1 - x0) / 2, (z1 - z0) / 2
    footprint = {(x, z) for (x, y, z) in building.blocks if y <= 1}
    pad = {(x + dx, z + dz) for (x, z) in footprint for dx in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2)}
    top_blk, sub_blk = GROUND[dna.terrain]
    m = VoxelModel()
    t = dna.terrain

    def norm_dist(x, z) -> float:
        return math.sqrt(((x - cx) / rx) ** 2 + ((z - cz) / rz) ** 2)

    for x in range(x0, x1 + 1):
        for z in range(z0, z1 + 1):
            nd = norm_dist(x, z)
            noise = (math.sin(x * 0.31 + dna.seed % 7) + math.cos(z * 0.27 + dna.seed % 11)) * 0.06
            if t in ("island", "sea"):
                land_r = 0.62 if t == "island" else 0.0
                if (x, z) in pad or nd + noise < land_r:
                    edge = nd + noise > land_r - 0.08 and (x, z) not in pad
                    m.set(x, -1, z, "minecraft:sand" if edge and t == "island" else top_blk, "terrain", "ground")
                    m.set(x, -2, z, "minecraft:sandstone" if t == "island" else sub_blk, "terrain", "ground")
                else:
                    for y in (-1, -2, -3):
                        m.set(x, y, z, "minecraft:water", "terrain", "water")
                    m.set(x, -4, z, "minecraft:sand" if nd < 0.9 else "minecraft:gravel", "terrain", "seabed")
            elif t == "mountain":
                m.set(x, -1, z, top_blk, "terrain", "ground")
                # 북쪽 배경 산맥
                if z < bz0 - 3 and (x, z) not in pad:
                    hgt = int(max(0.0, (bz0 - z) / max(1, margin)) * (margin * 1.2) * (0.7 + 0.3 * math.sin(x * 0.15 + dna.seed)))
                    for y in range(0, hgt):
                        blk = "minecraft:stone" if y < hgt - 1 else ("minecraft:snow_block" if hgt > margin * 0.8 else top_blk)
                        m.set(x, y, z, blk, "terrain", "mountain")
            elif t == "lava":
                if (x, z) not in pad and 0.55 < nd + noise < 0.7:
                    m.set(x, -1, z, "minecraft:lava", "terrain", "lava")
                    m.set(x, -2, z, "minecraft:magma_block", "terrain", "lava")
                else:
                    m.set(x, -1, z, top_blk if rng.random() > 0.1 else "minecraft:magma_block", "terrain", "ground")
            else:
                if nd + noise <= 1.0 or (x, z) in pad:
                    m.set(x, -1, z, top_blk, "terrain", "ground")

    # 길: 정문(남쪽)에서 가장자리까지
    if t not in ("sea",):
        for z in range(bz1 + 1, z1 + 1):
            for dx in (-1, 0, 1):
                if (int(cx) + dx, z) in footprint:
                    continue
                cur = m.get(int(cx) + dx, -1, z)
                if cur and cur.block != "minecraft:water" and cur.block != "minecraft:lava":
                    m.set(int(cx) + dx, -1, z, "minecraft:dirt_path" if t in ("plains", "forest", "island", "mountain") else "minecraft:smooth_stone",
                          "landscape", "path")
    else:
        for z in range(bz1 + 1, z1 + 1):
            for dx in (-1, 0, 1):
                m.set(int(cx) + dx, -1, z, "minecraft:spruce_planks", "landscape", "pier")

    # 나무 / 식생
    kinds = {"plains": ["oak", "birch"], "forest": ["oak", "spruce", "birch"], "island": ["jungle", "oak"],
             "mountain": ["spruce"], "snow": ["spruce"], "desert": [], "sea": [], "lava": []}[t]
    if dna.style in ("japanese", "korean") and kinds:
        kinds = ["cherry"]
    density = {"forest": 0.03, "plains": 0.008, "island": 0.012, "mountain": 0.01, "snow": 0.012}.get(t, 0)
    for x in range(x0 + 2, x1 - 1):
        for z in range(z0 + 2, z1 - 1):
            if (x, z) in pad or not kinds:
                continue
            g = m.get(x, -1, z)
            if not g or g.block not in ("minecraft:grass_block", "minecraft:snow_block"):
                continue
            if m.get(x, 0, z):
                continue
            if rng.random() < density:
                tree(m, x, z, 0, rng.choice(kinds), rng)
            elif t in ("plains", "forest", "island") and rng.random() < 0.05:
                m.set(x, 0, z, rng.choice(["minecraft:poppy", "minecraft:dandelion", "minecraft:oxeye_daisy",
                                           "minecraft:cornflower", "minecraft:azure_bluet"]), "landscape", "flowers",
                      overwrite=False)
            elif t == "snow" and rng.random() < 0.3:
                m.set(x, 0, z, "minecraft:snow[layers=1]", "landscape", "snow", overwrite=False)
    if t == "desert":
        for _ in range(int((x1 - x0) * (z1 - z0) * 0.004)):
            x, z = rng.randint(x0 + 2, x1 - 2), rng.randint(z0 + 2, z1 - 2)
            if (x, z) in pad:
                continue
            m.set(x, -1, z, "minecraft:sand", "terrain", "ground")
            for y in range(0, rng.randint(1, 3)):
                m.set(x, y, z, "minecraft:cactus", "landscape", "cactus", overwrite=False)
    # 연못 (평원/숲)
    if t in ("plains", "forest") and rng.random() < 0.7:
        px, pz, pr = int(x0 + margin * 0.5), int(z1 - margin * 0.5), max(2, margin // 4)
        for x in range(px - pr, px + pr + 1):
            for z in range(pz - pr, pz + pr + 1):
                if (x - px) ** 2 + (z - pz) ** 2 <= pr * pr and (x, z) not in pad:
                    m.remove(x, 0, z)
                    m.set(x, -1, z, "minecraft:water", "terrain", "pond")
                    m.set(x, -2, z, "minecraft:water", "terrain", "pond")
    return m
