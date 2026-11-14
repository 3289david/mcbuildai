"""설계도 파일 입출력.

- Sponge Schematic v2/v3 (.schem) 쓰기/읽기 (WorldEdit 호환)
- .mcfunction 읽기 (genMC 내보내기 결과를 블록 모델로 변환)
- .mcfunction 쓰기 (데이터팩용)
"""
from __future__ import annotations

import gzip
import re
from pathlib import Path

import nbtlib
from nbtlib.tag import ByteArray, Compound, Int, IntArray, Short, String

from .voxel import VoxelModel

# Minecraft Java DataVersion
DATA_VERSIONS = {"1.20.4": 3700, "1.20.6": 3839, "1.21": 3953, "1.21.1": 3955,
                 "1.21.3": 4082, "1.21.4": 4189, "1.21.5": 4325}


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read_varints(data: bytes, count: int) -> list[int]:
    vals, i = [], 0
    for _ in range(count):
        v, shift = 0, 0
        while True:
            b = data[i] & 0xFF
            i += 1
            v |= (b & 0x7F) << shift
            if not b & 0x80:
                break
            shift += 7
        vals.append(v)
    return vals


def write_schem(model: VoxelModel, path: Path, mc_version: str = "1.21.1", sponge_version: int | None = None) -> Path:
    data_version = DATA_VERSIONS.get(mc_version, 3955)
    if sponge_version is None:
        sponge_version = 3 if data_version >= 3953 else 2
    (x0, y0, z0), (x1, y1, z1) = model.bounds()
    W, H, L = x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1
    palette: dict[str, int] = {"minecraft:air": 0}
    idx = [0] * (W * H * L)
    for (x, y, z), v in model.items():
        pid = palette.setdefault(v.block, len(palette))
        idx[(x - x0) + (z - z0) * W + (y - y0) * W * L] = pid
    data = bytearray()
    for v in idx:
        data += _varint(v)
    pal_tag = Compound({k: Int(v) for k, v in palette.items()})
    raw = [b - 256 if b > 127 else b for b in data]
    if sponge_version == 3:
        root = Compound({
            "Schematic": Compound({
                "Version": Int(3), "DataVersion": Int(data_version),
                "Width": Short(W), "Height": Short(H), "Length": Short(L),
                "Offset": IntArray([x0, y0, z0]),
                "Blocks": Compound({"Palette": pal_tag, "Data": ByteArray(raw)}),
                "Metadata": Compound({"Generator": String("mcbuildai")}),
            })
        })
        f = nbtlib.File(root)
    else:
        root = Compound({
            "Version": Int(2), "DataVersion": Int(data_version),
            "Width": Short(W), "Height": Short(H), "Length": Short(L),
            "Offset": IntArray([x0, y0, z0]),
            "PaletteMax": Int(len(palette)), "Palette": pal_tag,
            "BlockData": ByteArray(raw),
        })
        f = nbtlib.File(root, root_name="Schematic")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f.save(path, gzipped=True)
    return path


def read_schem(path: Path) -> VoxelModel:
    f = nbtlib.load(path)
    root = f["Schematic"] if "Schematic" in f else f
    W, H, L = int(root["Width"]), int(root["Height"]), int(root["Length"])
    if "Blocks" in root:  # v3
        pal = root["Blocks"]["Palette"]; data = bytes(b & 0xFF for b in root["Blocks"]["Data"])
    else:
        pal = root["Palette"]; data = bytes(b & 0xFF for b in root["BlockData"])
    inv = {int(v): k for k, v in pal.items()}
    vals = _read_varints(data, W * H * L)
    off = [int(v) for v in root.get("Offset", [0, 0, 0])]
    m = VoxelModel()
    for i, pid in enumerate(vals):
        name = inv.get(pid, "minecraft:air")
        if name.endswith(":air") or name.endswith("cave_air") or name.endswith("void_air"):
            continue
        y, rem = divmod(i, W * L)
        z, x = divmod(rem, W)
        m.set(x + off[0], y + off[1], z + off[2], str(name), "wall", "main")
    return m


# ---------------------------------------------------------------------------
# mcfunction
# ---------------------------------------------------------------------------
_COORD = r"(~?-?\d*\.?\d*)"
_SETBLOCK = re.compile(rf"^\s*/?setblock\s+{_COORD}\s+{_COORD}\s+{_COORD}\s+(\S+?)(?:\s+(replace|keep|destroy|strict))?\s*$")
_FILL = re.compile(rf"^\s*/?fill\s+{_COORD}\s+{_COORD}\s+{_COORD}\s+{_COORD}\s+{_COORD}\s+{_COORD}\s+(\S+?)(?:\s+(replace|keep|destroy|hollow|outline|strict)(?:\s+\S+)?)?\s*$")


def _num(tok: str) -> int:
    t = tok.lstrip("~")
    return int(float(t)) if t not in ("", "-") else 0


def _block_with_nbt(tok: str) -> str:
    # 블록 엔티티 NBT({...})는 제거한다
    return tok.split("{", 1)[0]


def read_mcfunction(text: str) -> tuple[VoxelModel, list[str]]:
    """setblock/fill 명령을 블록 모델로 변환. 해석하지 못한 줄 목록도 돌려준다."""
    m = VoxelModel()
    skipped: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        # execute positioned ~ ~ ~ run setblock ... 형태 처리
        if s.startswith("execute") and " run " in s:
            s = s.split(" run ", 1)[1]
        mm = _SETBLOCK.match(s)
        if mm:
            x, y, z, blk = _num(mm[1]), _num(mm[2]), _num(mm[3]), _block_with_nbt(mm[4])
            m.set(x, y, z, blk)
            continue
        mm = _FILL.match(s)
        if mm:
            xs = [_num(mm[i]) for i in range(1, 7)]
            blk = _block_with_nbt(mm[7])
            mode = mm[8] or "replace"
            if blk.endswith(":air") or blk == "air":
                m.clear(*xs)
            else:
                if mode == "hollow":
                    xa, xb = sorted((xs[0], xs[3])); ya, yb = sorted((xs[1], xs[4])); za, zb = sorted((xs[2], xs[5]))
                    if xb - xa > 1 and yb - ya > 1 and zb - za > 1:
                        m.clear(xa + 1, ya + 1, za + 1, xb - 1, yb - 1, zb - 1)
                m.fill(*xs, block=blk, hollow=mode in ("hollow", "outline"))
            continue
        skipped.append(line)
    return m, skipped


def write_mcfunction(model: VoxelModel, path: Path) -> Path:
    lines = [f"setblock ~{x} ~{y} ~{z} {v.block}" for (x, y, z), v in sorted(model.items(), key=lambda kv: (kv[0][1], kv[0][0], kv[0][2]))]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return Path(path)
