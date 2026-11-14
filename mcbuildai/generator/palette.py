"""블록 팔레트. 실제 Minecraft Java 1.21 블록 ID 를 사용한다.

중력 블록(모래, 자갈, 콘크리트 가루)은 건축물 팔레트에 넣지 않는다.
판유리/울타리처럼 이웃 연결 상태가 필요한 블록도 setblock 시 모양이 깨지므로 제외한다.
"""
from __future__ import annotations

PALETTES: dict[str, dict[str, str]] = {
    "oak_cobble": {
        "ko": "참나무와 조약돌",
        "wall": "minecraft:oak_planks", "accent": "minecraft:stripped_oak_log",
        "trim": "minecraft:cobblestone", "foundation": "minecraft:stone_bricks",
        "roof": "minecraft:spruce_planks", "roof_stair": "minecraft:spruce_stairs",
        "roof_slab": "minecraft:spruce_slab", "pillar": "minecraft:oak_log",
        "floor": "minecraft:spruce_planks", "glass": "minecraft:glass",
        "light": "minecraft:lantern", "detail": "minecraft:cobblestone_wall",
    },
    "spruce_stone": {
        "ko": "가문비나무와 석재",
        "wall": "minecraft:spruce_planks", "accent": "minecraft:stripped_spruce_log",
        "trim": "minecraft:stone_bricks", "foundation": "minecraft:cobblestone",
        "roof": "minecraft:dark_oak_planks", "roof_stair": "minecraft:dark_oak_stairs",
        "roof_slab": "minecraft:dark_oak_slab", "pillar": "minecraft:spruce_log",
        "floor": "minecraft:oak_planks", "glass": "minecraft:glass",
        "light": "minecraft:lantern", "detail": "minecraft:stone_brick_wall",
    },
    "stone_castle": {
        "ko": "성채 석재",
        "wall": "minecraft:stone_bricks", "accent": "minecraft:mossy_stone_bricks",
        "trim": "minecraft:chiseled_stone_bricks", "foundation": "minecraft:cobblestone",
        "roof": "minecraft:deepslate_tiles", "roof_stair": "minecraft:deepslate_tile_stairs",
        "roof_slab": "minecraft:deepslate_tile_slab", "pillar": "minecraft:polished_andesite",
        "floor": "minecraft:spruce_planks", "glass": "minecraft:gray_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:stone_brick_wall",
    },
    "deepslate_gothic": {
        "ko": "심층암 고딕",
        "wall": "minecraft:deepslate_bricks", "accent": "minecraft:polished_deepslate",
        "trim": "minecraft:chiseled_deepslate", "foundation": "minecraft:cobbled_deepslate",
        "roof": "minecraft:deepslate_tiles", "roof_stair": "minecraft:deepslate_tile_stairs",
        "roof_slab": "minecraft:deepslate_tile_slab", "pillar": "minecraft:polished_basalt",
        "floor": "minecraft:dark_oak_planks", "glass": "minecraft:purple_stained_glass",
        "light": "minecraft:soul_lantern", "detail": "minecraft:deepslate_brick_wall",
    },
    "blackstone_dark": {
        "ko": "흑암석",
        "wall": "minecraft:polished_blackstone_bricks", "accent": "minecraft:gilded_blackstone",
        "trim": "minecraft:chiseled_polished_blackstone", "foundation": "minecraft:blackstone",
        "roof": "minecraft:polished_blackstone", "roof_stair": "minecraft:polished_blackstone_brick_stairs",
        "roof_slab": "minecraft:polished_blackstone_brick_slab", "pillar": "minecraft:basalt",
        "floor": "minecraft:crimson_planks", "glass": "minecraft:red_stained_glass",
        "light": "minecraft:shroomlight", "detail": "minecraft:polished_blackstone_wall",
    },
    "white_marble": {
        "ko": "백색 대리석과 금",
        "wall": "minecraft:quartz_block", "accent": "minecraft:gold_block",
        "trim": "minecraft:chiseled_quartz_block", "foundation": "minecraft:smooth_stone",
        "roof": "minecraft:smooth_quartz", "roof_stair": "minecraft:quartz_stairs",
        "roof_slab": "minecraft:quartz_slab", "pillar": "minecraft:quartz_pillar",
        "floor": "minecraft:polished_diorite", "glass": "minecraft:white_stained_glass",
        "light": "minecraft:sea_lantern", "detail": "minecraft:diorite_wall",
    },
    "sandstone_desert": {
        "ko": "사암",
        "wall": "minecraft:sandstone", "accent": "minecraft:orange_terracotta",
        "trim": "minecraft:chiseled_sandstone", "foundation": "minecraft:smooth_sandstone",
        "roof": "minecraft:cut_sandstone", "roof_stair": "minecraft:smooth_sandstone_stairs",
        "roof_slab": "minecraft:smooth_sandstone_slab", "pillar": "minecraft:cut_sandstone",
        "floor": "minecraft:terracotta", "glass": "minecraft:orange_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:sandstone_wall",
    },
    "mud_adobe": {
        "ko": "진흙 벽돌",
        "wall": "minecraft:mud_bricks", "accent": "minecraft:stripped_mangrove_log",
        "trim": "minecraft:packed_mud", "foundation": "minecraft:terracotta",
        "roof": "minecraft:packed_mud", "roof_stair": "minecraft:mud_brick_stairs",
        "roof_slab": "minecraft:mud_brick_slab", "pillar": "minecraft:mangrove_log",
        "floor": "minecraft:mangrove_planks", "glass": "minecraft:brown_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:mud_brick_wall",
    },
    "japanese_dark": {
        "ko": "일본식 흑목",
        "wall": "minecraft:white_terracotta", "accent": "minecraft:stripped_dark_oak_log",
        "trim": "minecraft:dark_oak_planks", "foundation": "minecraft:stone_bricks",
        "roof": "minecraft:deepslate_tiles", "roof_stair": "minecraft:deepslate_tile_stairs",
        "roof_slab": "minecraft:deepslate_tile_slab", "pillar": "minecraft:dark_oak_log",
        "floor": "minecraft:bamboo_planks", "glass": "minecraft:white_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:stone_brick_wall",
    },
    "cherry_blossom": {
        "ko": "벚나무",
        "wall": "minecraft:cherry_planks", "accent": "minecraft:stripped_cherry_log",
        "trim": "minecraft:white_terracotta", "foundation": "minecraft:stone_bricks",
        "roof": "minecraft:pink_terracotta", "roof_stair": "minecraft:cherry_stairs",
        "roof_slab": "minecraft:cherry_slab", "pillar": "minecraft:cherry_log",
        "floor": "minecraft:cherry_planks", "glass": "minecraft:pink_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:stone_brick_wall",
    },
    "red_imperial": {
        "ko": "붉은 궁궐",
        "wall": "minecraft:red_terracotta", "accent": "minecraft:gold_block",
        "trim": "minecraft:red_nether_bricks", "foundation": "minecraft:polished_andesite",
        "roof": "minecraft:dark_prismarine", "roof_stair": "minecraft:dark_prismarine_stairs",
        "roof_slab": "minecraft:dark_prismarine_slab", "pillar": "minecraft:red_concrete",
        "floor": "minecraft:dark_oak_planks", "glass": "minecraft:yellow_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:red_nether_brick_wall",
    },
    "hanok_giwa": {
        "ko": "한옥 기와",
        "wall": "minecraft:white_concrete", "accent": "minecraft:stripped_spruce_log",
        "trim": "minecraft:spruce_planks", "foundation": "minecraft:stone_bricks",
        "roof": "minecraft:deepslate_tiles", "roof_stair": "minecraft:deepslate_tile_stairs",
        "roof_slab": "minecraft:deepslate_tile_slab", "pillar": "minecraft:spruce_log",
        "floor": "minecraft:spruce_planks", "glass": "minecraft:white_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:stone_brick_wall",
    },
    "quartz_modern": {
        "ko": "모던 화이트",
        "wall": "minecraft:white_concrete", "accent": "minecraft:light_gray_concrete",
        "trim": "minecraft:smooth_quartz", "foundation": "minecraft:smooth_stone",
        "roof": "minecraft:smooth_stone", "roof_stair": "minecraft:smooth_quartz_stairs",
        "roof_slab": "minecraft:smooth_stone_slab", "pillar": "minecraft:quartz_pillar",
        "floor": "minecraft:birch_planks", "glass": "minecraft:light_blue_stained_glass",
        "light": "minecraft:sea_lantern", "detail": "minecraft:andesite_wall",
    },
    "concrete_gray": {
        "ko": "노출 콘크리트",
        "wall": "minecraft:gray_concrete", "accent": "minecraft:black_concrete",
        "trim": "minecraft:polished_andesite", "foundation": "minecraft:stone",
        "roof": "minecraft:gray_concrete", "roof_stair": "minecraft:polished_andesite_stairs",
        "roof_slab": "minecraft:polished_andesite_slab", "pillar": "minecraft:black_concrete",
        "floor": "minecraft:dark_oak_planks", "glass": "minecraft:gray_stained_glass",
        "light": "minecraft:sea_lantern", "detail": "minecraft:andesite_wall",
    },
    "copper_future": {
        "ko": "구리 미래도시",
        "wall": "minecraft:white_concrete", "accent": "minecraft:waxed_oxidized_cut_copper",
        "trim": "minecraft:polished_blackstone", "foundation": "minecraft:polished_deepslate",
        "roof": "minecraft:waxed_cut_copper", "roof_stair": "minecraft:waxed_cut_copper_stairs",
        "roof_slab": "minecraft:waxed_cut_copper_slab", "pillar": "minecraft:waxed_copper_block",
        "floor": "minecraft:polished_deepslate", "glass": "minecraft:cyan_stained_glass",
        "light": "minecraft:sea_lantern", "detail": "minecraft:polished_blackstone_wall",
    },
    "prismarine_atlantis": {
        "ko": "해저 프리즈머린",
        "wall": "minecraft:prismarine_bricks", "accent": "minecraft:dark_prismarine",
        "trim": "minecraft:prismarine", "foundation": "minecraft:dark_prismarine",
        "roof": "minecraft:dark_prismarine", "roof_stair": "minecraft:prismarine_brick_stairs",
        "roof_slab": "minecraft:prismarine_brick_slab", "pillar": "minecraft:prismarine",
        "floor": "minecraft:warped_planks", "glass": "minecraft:light_blue_stained_glass",
        "light": "minecraft:sea_lantern", "detail": "minecraft:prismarine_wall",
    },
    "purpur_fantasy": {
        "ko": "보랏빛 판타지",
        "wall": "minecraft:purpur_block", "accent": "minecraft:amethyst_block",
        "trim": "minecraft:end_stone_bricks", "foundation": "minecraft:end_stone_bricks",
        "roof": "minecraft:purpur_block", "roof_stair": "minecraft:purpur_stairs",
        "roof_slab": "minecraft:purpur_slab", "pillar": "minecraft:purpur_pillar",
        "floor": "minecraft:warped_planks", "glass": "minecraft:magenta_stained_glass",
        "light": "minecraft:end_rod", "detail": "minecraft:end_stone_brick_wall",
    },
    "brick_copper": {
        "ko": "벽돌과 구리",
        "wall": "minecraft:bricks", "accent": "minecraft:waxed_exposed_cut_copper",
        "trim": "minecraft:iron_block", "foundation": "minecraft:polished_blackstone",
        "roof": "minecraft:waxed_exposed_cut_copper", "roof_stair": "minecraft:waxed_exposed_cut_copper_stairs",
        "roof_slab": "minecraft:waxed_exposed_cut_copper_slab", "pillar": "minecraft:polished_blackstone_bricks",
        "floor": "minecraft:spruce_planks", "glass": "minecraft:orange_stained_glass",
        "light": "minecraft:lantern", "detail": "minecraft:brick_wall",
    },
}

# 미리보기 렌더링용 블록 대표 색 (RGB, 텍스처 평균에 가까운 근사값)
BLOCK_COLORS: dict[str, tuple[int, int, int]] = {
    "oak_planks": (162, 130, 78), "spruce_planks": (115, 85, 49), "dark_oak_planks": (67, 43, 20),
    "birch_planks": (192, 175, 121), "cherry_planks": (226, 178, 172), "bamboo_planks": (194, 173, 80),
    "mangrove_planks": (117, 54, 48), "crimson_planks": (101, 48, 70), "warped_planks": (43, 104, 99),
    "oak_log": (109, 85, 50), "spruce_log": (58, 37, 16), "dark_oak_log": (60, 46, 26),
    "cherry_log": (54, 33, 44), "mangrove_log": (84, 66, 41),
    "stripped_oak_log": (177, 144, 86), "stripped_spruce_log": (115, 89, 52),
    "stripped_dark_oak_log": (96, 76, 49), "stripped_cherry_log": (215, 145, 148),
    "stripped_mangrove_log": (119, 54, 47),
    "cobblestone": (127, 127, 127), "stone": (125, 125, 125), "stone_bricks": (122, 121, 122),
    "mossy_stone_bricks": (115, 121, 105), "chiseled_stone_bricks": (119, 118, 119),
    "smooth_stone": (158, 158, 158), "polished_andesite": (132, 134, 133),
    "polished_diorite": (192, 193, 194), "deepslate_bricks": (70, 70, 71),
    "deepslate_tiles": (54, 54, 55), "polished_deepslate": (72, 72, 73),
    "chiseled_deepslate": (54, 54, 54), "cobbled_deepslate": (77, 77, 80),
    "polished_basalt": (99, 98, 100), "basalt": (80, 81, 86),
    "polished_blackstone_bricks": (48, 42, 49), "polished_blackstone": (53, 48, 56),
    "chiseled_polished_blackstone": (53, 48, 56), "gilded_blackstone": (55, 42, 38),
    "blackstone": (42, 36, 41), "quartz_block": (235, 229, 222), "smooth_quartz": (235, 229, 222),
    "chiseled_quartz_block": (231, 226, 218), "quartz_pillar": (235, 230, 224),
    "gold_block": (246, 208, 61), "iron_block": (220, 220, 220),
    "sandstone": (216, 203, 155), "cut_sandstone": (217, 206, 159), "chiseled_sandstone": (216, 202, 154),
    "smooth_sandstone": (223, 214, 170), "terracotta": (152, 94, 67), "orange_terracotta": (161, 83, 37),
    "white_terracotta": (209, 178, 161), "pink_terracotta": (161, 78, 78), "red_terracotta": (143, 61, 46),
    "mud_bricks": (137, 103, 79), "packed_mud": (142, 106, 79), "red_nether_bricks": (69, 7, 9),
    "dark_prismarine": (51, 91, 75), "prismarine_bricks": (99, 171, 158), "prismarine": (99, 156, 151),
    "white_concrete": (207, 213, 214), "light_gray_concrete": (125, 125, 115), "gray_concrete": (54, 57, 61),
    "black_concrete": (8, 10, 15), "red_concrete": (142, 32, 32),
    "waxed_oxidized_cut_copper": (79, 153, 126), "waxed_cut_copper": (191, 106, 80),
    "waxed_copper_block": (192, 107, 79), "waxed_exposed_cut_copper": (154, 121, 101),
    "purpur_block": (169, 125, 169), "purpur_pillar": (171, 129, 171), "amethyst_block": (133, 97, 191),
    "end_stone_bricks": (218, 224, 162), "bricks": (150, 97, 83),
    "glass": (200, 220, 230), "white_stained_glass": (255, 255, 255), "gray_stained_glass": (76, 76, 76),
    "purple_stained_glass": (127, 63, 178), "red_stained_glass": (153, 51, 51),
    "orange_stained_glass": (216, 127, 51), "brown_stained_glass": (102, 76, 51),
    "pink_stained_glass": (242, 127, 165), "yellow_stained_glass": (229, 229, 51),
    "light_blue_stained_glass": (102, 153, 216), "cyan_stained_glass": (76, 127, 153),
    "magenta_stained_glass": (178, 76, 216),
    "lantern": (106, 91, 83), "soul_lantern": (71, 99, 114), "sea_lantern": (172, 199, 190),
    "shroomlight": (240, 146, 70), "end_rod": (220, 210, 200), "glowstone": (171, 131, 84),
    "grass_block": (95, 159, 53), "dirt": (134, 96, 67), "sand": (219, 207, 163), "red_sand": (190, 102, 33),
    "snow_block": (249, 254, 254), "snow": (249, 254, 254), "water": (63, 118, 228), "lava": (207, 92, 20),
    "oak_leaves": (60, 120, 40), "spruce_leaves": (55, 90, 55), "cherry_leaves": (229, 172, 194),
    "birch_leaves": (110, 150, 70), "jungle_leaves": (50, 130, 30),
    "magma_block": (142, 63, 31), "netherrack": (97, 38, 38), "gravel": (131, 127, 126),
    "moss_block": (89, 109, 45), "podzol": (91, 63, 24), "coarse_dirt": (119, 85, 59),
    "cactus": (85, 127, 43), "dirt_path": (148, 122, 65), "chain": (60, 64, 75),
}


def block_name(state: str) -> str:
    """'minecraft:oak_stairs[facing=north]' -> 'oak_stairs'"""
    s = state.split("[", 1)[0]
    return s.split(":", 1)[-1]


def block_color(state: str) -> tuple[int, int, int]:
    name = block_name(state)
    if name in BLOCK_COLORS:
        return BLOCK_COLORS[name]
    for suffix in ("_stairs", "_slab", "_wall"):
        if name.endswith(suffix):
            base = name[: -len(suffix)]
            for cand in (base, base + "s", base + "_block", base.replace("brick", "bricks"),
                         base.replace("tile", "tiles"), base.replace("_brick", "_bricks")):
                if cand in BLOCK_COLORS:
                    return BLOCK_COLORS[cand]
    # 알 수 없는 블록은 이름 해시로 일관된 회색조를 준다 (미리보기 전용)
    h = sum(ord(c) for c in name) % 80
    return (110 + h, 110 + h, 110 + h)


GRAVITY_BLOCKS = {"sand", "red_sand", "gravel", "anvil", "dragon_egg", "scaffolding",
                  "pointed_dripstone", "suspicious_sand", "suspicious_gravel"}


def is_gravity(state: str) -> bool:
    n = block_name(state)
    return n in GRAVITY_BLOCKS or n.endswith("_concrete_powder")
