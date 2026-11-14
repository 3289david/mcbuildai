"""외부에서 가져온 블록 모델(genMC, .schem)에 부품/구역 라벨을 붙인다.

부품 라벨은 재료별 순차 건축, 구역 라벨은 구역별 건축 연출에 쓰인다.
"""
from __future__ import annotations

from .palette import block_name
from .voxel import VoxelModel

TERRAIN_HINTS = ("grass_block", "dirt", "sand", "water", "lava", "snow_block", "podzol", "gravel",
                 "coarse_dirt", "moss_block", "mud", "clay", "netherrack")
LANDSCAPE_HINTS = ("leaves", "flower", "poppy", "dandelion", "tulip", "short_grass", "tall_grass", "fern",
                   "bush", "sapling", "cactus", "sugar_cane", "vine", "lily", "daisy", "orchid", "allium",
                   "bluet", "cornflower", "lilac", "rose", "peony")


def normalize(model: VoxelModel, ground_to_zero: bool = True) -> VoxelModel:
    """x, z 중심을 0 으로, 최저 y 를 0 으로 맞춘다."""
    (x0, y0, z0), (x1, y1, z1) = model.bounds()
    dx, dz = -((x0 + x1) // 2), -((z0 + z1) // 2)
    dy = -y0 if ground_to_zero else 0
    out = VoxelModel()
    out.merge(model, dx, dy, dz)
    return out


def classify_part(name: str, y: int, top_y: int, is_log: bool) -> str:
    if any(h in name for h in LANDSCAPE_HINTS):
        return "landscape"
    if any(name == h or name.startswith(h) for h in TERRAIN_HINTS):
        return "terrain" if y <= 1 else "decor"
    if "glass" in name:
        return "window"
    if name.endswith(("_stairs", "_slab")) and y >= top_y * 0.55:
        return "roof"
    if name.endswith(("_stairs",)):
        return "stair"
    if is_log or "pillar" in name:
        return "pillar"
    if any(k in name for k in ("lantern", "torch", "lamp", "sea_lantern", "glowstone", "end_rod", "candle",
                                "chain", "banner", "carpet", "bed", "chest", "barrel", "bookshelf", "table",
                                "door", "trapdoor", "fence", "_wall", "flower_pot", "pane")):
        return "decor"
    if y == 0:
        return "foundation"
    if any(k in name for k in ("planks",)) and y % 5 == 0:
        return "floor"
    if y >= top_y * 0.75 and any(k in name for k in ("tile", "shingle", "copper", "roof")):
        return "roof"
    return "wall"


def auto_label(model: VoxelModel) -> VoxelModel:
    if not len(model):
        return model
    (x0, y0, z0), (x1, y1, z1) = model.bounds()
    top = max(1, y1)
    cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
    out = VoxelModel()
    for (x, y, z), v in model.items():
        name = block_name(v.block)
        part = classify_part(name, y, top, name.endswith(("_log", "_wood", "_stem")))
        if part in ("terrain", "landscape"):
            sec = "surroundings"
        elif y >= top * 0.7:
            sec = "upper"
        else:
            sec = ("north" if z < cz else "south") + "_" + ("west" if x < cx else "east")
        out.blocks[(x, y, z)] = type(v)(v.block, part, sec)
    return out
