"""블록 모델 아이소메트릭 미리보기 렌더러 (Pillow). 보이는 면만 그려 대형 건축물도 빠르게 처리한다."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .palette import block_color
from .voxel import VoxelModel


def _shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def render_preview(model: VoxelModel, path: Path, size: int = 1024, background=(24, 26, 32),
                   rotate: int = 0) -> Path:
    """rotate: 0~3 (90도 단위 회전)"""
    if not len(model):
        raise ValueError("빈 모델은 렌더링할 수 없습니다")
    blocks = model.blocks

    def rot(x, z):
        for _ in range(rotate % 4):
            x, z = -z, x
        return x, z

    pts = {}
    for (x, y, z), v in blocks.items():
        rx, rz = rot(x, z)
        pts[(rx, y, rz)] = v.block
    occupied = set(pts)
    visible = []
    for (x, y, z), b in pts.items():
        top = (x, y + 1, z) not in occupied
        left = (x, y, z + 1) not in occupied
        right = (x + 1, y, z) not in occupied
        if top or left or right:
            visible.append((x, y, z, b, top, left, right))
    xs = [p[0] for p in visible]; ys = [p[1] for p in visible]; zs = [p[2] for p in visible]
    # 아이소메트릭 투영 범위 계산 (단위 크기 1)
    def proj(x, y, z, s):
        return ((x - z) * s * 0.866, (x + z) * s * 0.5 - y * s)

    s = 1.0
    corners = [proj(x, y, z, s) for x in (min(xs), max(xs) + 1) for y in (min(ys), max(ys) + 1) for z in (min(zs), max(zs) + 1)]
    minx = min(c[0] for c in corners); maxx = max(c[0] for c in corners)
    miny = min(c[1] for c in corners); maxy = max(c[1] for c in corners)
    scale = (size * 0.9) / max(maxx - minx, maxy - miny, 1)
    ox = size / 2 - (minx + maxx) / 2 * scale
    oy = size / 2 - (miny + maxy) / 2 * scale
    img = Image.new("RGB", (size, size), background)
    dr = ImageDraw.Draw(img)
    visible.sort(key=lambda p: (p[0] + p[2], p[1]))

    def P(x, y, z):
        px, py = proj(x, y, z, scale)
        return (px + ox, py + oy)

    for x, y, z, b, top, left, right in visible:
        c = block_color(b)
        if top:
            dr.polygon([P(x, y + 1, z), P(x + 1, y + 1, z), P(x + 1, y + 1, z + 1), P(x, y + 1, z + 1)], fill=c)
        if left:
            dr.polygon([P(x, y, z + 1), P(x + 1, y, z + 1), P(x + 1, y + 1, z + 1), P(x, y + 1, z + 1)], fill=_shade(c, 0.78))
        if right:
            dr.polygon([P(x + 1, y, z), P(x + 1, y, z + 1), P(x + 1, y + 1, z + 1), P(x + 1, y + 1, z)], fill=_shade(c, 0.62))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    return path
