"""중복 영상 검사: 프레임 dHash 를 비교해 이미 만든 영상과 지나치게 비슷한지 판단한다."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from mcbuildai.config import Config
from mcbuildai.db import DB
from .ff import bins, duration

N_FRAMES = 12


def video_hash(path: Path, cfg: Config | None = None) -> str:
    ff, _ = bins(cfg)
    dur = duration(path, cfg)
    hashes = []
    for i in range(N_FRAMES):
        t = dur * (i + 0.5) / N_FRAMES
        p = subprocess.run([ff, "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                            "-vf", "scale=9:8,format=gray", "-f", "rawvideo", "-"], capture_output=True)
        px = np.frombuffer(p.stdout, dtype=np.uint8)
        if px.size != 72:
            hashes.append("0" * 16)
            continue
        g = px.reshape(8, 9).astype(int)
        bits = (g[:, 1:] > g[:, :-1]).flatten()
        hashes.append(f"{int(''.join('1' if b else '0' for b in bits), 2):016x}")
    return "".join(hashes)


def distance(a: str, b: str) -> float:
    """평균 해밍 거리 (0~64). 작을수록 비슷하다."""
    if not a or not b or len(a) != len(b):
        return 64.0
    ds = []
    for i in range(0, len(a), 16):
        ds.append(bin(int(a[i:i + 16], 16) ^ int(b[i:i + 16], 16)).count("1"))
    return float(sum(ds)) / len(ds)


def find_duplicates(db: DB, phash: str, variant: str, exclude_building: int | None = None,
                    threshold: float = 8.0) -> list[dict]:
    rows = db.query("SELECT id, building_id, path, phash FROM videos WHERE variant=? AND phash IS NOT NULL", (variant,))
    out = []
    for r in rows:
        if exclude_building and r["building_id"] == exclude_building:
            continue
        d = distance(phash, r["phash"])
        if d < threshold:
            out.append({**r, "distance": d})
    return out
