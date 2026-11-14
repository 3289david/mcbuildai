"""블록 단위 건축 모델. 좌표는 건축 원점 기준 상대 좌표 (x, y, z), y=0 이 지면 위 첫 층."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

# 부품(part) 종류. 재료별/구조별 순차 건축 연출에 사용한다.
PART_ORDER = ["terrain", "foundation", "pillar", "wall", "floor", "window",
              "stair", "interior", "roof", "decor", "landscape"]


@dataclass(slots=True)
class Voxel:
    block: str
    part: str
    section: str


class VoxelModel:
    def __init__(self) -> None:
        self.blocks: dict[tuple[int, int, int], Voxel] = {}

    # ---------------- 기본 조작 ----------------
    def set(self, x: int, y: int, z: int, block: str, part: str = "wall", section: str = "main",
            overwrite: bool = True) -> None:
        key = (int(x), int(y), int(z))
        if not overwrite and key in self.blocks:
            return
        if block in ("air", "minecraft:air"):
            self.blocks.pop(key, None)
            return
        if ":" not in block:
            block = "minecraft:" + block
        self.blocks[key] = Voxel(block, part, section)

    def get(self, x: int, y: int, z: int) -> Voxel | None:
        return self.blocks.get((x, y, z))

    def remove(self, x: int, y: int, z: int) -> None:
        self.blocks.pop((x, y, z), None)

    def fill(self, x0: int, y0: int, z0: int, x1: int, y1: int, z1: int, block: str,
             part: str = "wall", section: str = "main", hollow: bool = False, overwrite: bool = True) -> None:
        xa, xb = sorted((x0, x1)); ya, yb = sorted((y0, y1)); za, zb = sorted((z0, z1))
        for x in range(xa, xb + 1):
            for y in range(ya, yb + 1):
                for z in range(za, zb + 1):
                    if hollow and xa < x < xb and ya < y < yb and za < z < zb:
                        continue
                    self.set(x, y, z, block, part, section, overwrite)

    def clear(self, x0, y0, z0, x1, y1, z1) -> None:
        xa, xb = sorted((x0, x1)); ya, yb = sorted((y0, y1)); za, zb = sorted((z0, z1))
        for key in [k for k in self.blocks if xa <= k[0] <= xb and ya <= k[1] <= yb and za <= k[2] <= zb]:
            del self.blocks[key]

    def __len__(self) -> int:
        return len(self.blocks)

    def items(self) -> Iterator[tuple[tuple[int, int, int], Voxel]]:
        return iter(self.blocks.items())

    def merge(self, other: "VoxelModel", dx: int = 0, dy: int = 0, dz: int = 0, overwrite: bool = True) -> None:
        for (x, y, z), v in other.items():
            self.set(x + dx, y + dy, z + dz, v.block, v.part, v.section, overwrite)

    # ---------------- 정보 ----------------
    def bounds(self) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        if not self.blocks:
            return (0, 0, 0), (0, 0, 0)
        xs = [k[0] for k in self.blocks]; ys = [k[1] for k in self.blocks]; zs = [k[2] for k in self.blocks]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def size(self) -> tuple[int, int, int]:
        lo, hi = self.bounds()
        return hi[0] - lo[0] + 1, hi[1] - lo[1] + 1, hi[2] - lo[2] + 1

    def materials(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for v in self.blocks.values():
            name = v.block.split("[", 1)[0]
            out[name] = out.get(name, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def sections(self) -> list[str]:
        seen: list[str] = []
        for v in self.blocks.values():
            if v.section not in seen:
                seen.append(v.section)
        return seen

    def filter(self, parts: Iterable[str] | None = None, exclude_parts: Iterable[str] | None = None) -> "VoxelModel":
        m = VoxelModel()
        ps = set(parts) if parts else None
        ex = set(exclude_parts or ())
        for k, v in self.blocks.items():
            if (ps is None or v.part in ps) and v.part not in ex:
                m.blocks[k] = v
        return m

    # ---------------- 직렬화 ----------------
    def to_json(self, path: Path) -> None:
        palette: list[str] = []
        index: dict[str, int] = {}
        rows = []
        for (x, y, z), v in self.blocks.items():
            key = f"{v.block}|{v.part}|{v.section}"
            if key not in index:
                index[key] = len(palette)
                palette.append(key)
            rows.append([x, y, z, index[key]])
        Path(path).write_text(json.dumps({"format": "mcbuildai-voxel-1", "palette": palette, "blocks": rows}),
                              encoding="utf-8")

    @classmethod
    def from_json(cls, path: Path) -> "VoxelModel":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        m = cls()
        pal = [p.split("|") for p in d["palette"]]
        for x, y, z, i in d["blocks"]:
            b, part, sec = pal[i]
            m.blocks[(x, y, z)] = Voxel(b, part, sec)
        return m
