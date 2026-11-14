"""블록 단위 자동 건축 엔진.

- 초당 블록 수 제어, 진행률 기록, 중단/재개(체크포인트), 일시정지
- 버스트 연출(단계 사이 정지), 속도 프로파일
- 배치 검증과 잘못 놓인 블록 자동 수정
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from mcbuildai.timelapse.styles import speed_at
from .order import Placement, compress_runs
from .rcon import Rcon, RconError


@dataclass
class BuildStats:
    placed: int = 0
    commands: int = 0
    errors: int = 0
    fixed: int = 0
    verified: int = 0
    failed_checks: list = field(default_factory=list)
    started: float = field(default_factory=time.time)
    finished: float | None = None


class BuildEngine:
    def __init__(self, rcon: Rcon, origin: tuple[int, int, int], placements: list[Placement],
                 bps: float, speed_profile: str = "constant", groups: list[int] | None = None,
                 group_pause: float = 0.0, use_fill: bool = False, batch: int = 200,
                 on_progress: Callable[[int, int], None] | None = None,
                 should_pause: Callable[[], bool] | None = None,
                 stop_event: threading.Event | None = None):
        self.r = rcon
        self.ox, self.oy, self.oz = origin
        self.items = placements
        self.bps = max(1.0, bps)
        self.profile = speed_profile
        self.groups = set(groups or [])
        self.group_pause = group_pause
        self.use_fill = use_fill
        self.batch = batch
        self.on_progress = on_progress
        self.should_pause = should_pause or (lambda: False)
        self.stop_event = stop_event or threading.Event()
        self.stats = BuildStats()

    def _cmd_set(self, x, y, z, block) -> str:
        return f"setblock {self.ox + x} {self.oy + y} {self.oz + z} {block}"

    def run(self, start_index: int = 0) -> int:
        """start_index 부터 건축. 마지막으로 완료한 인덱스+1 을 돌려준다 (중단 시 재개 지점)."""
        n = len(self.items)
        i = start_index
        tick = 0.05
        last_report = 0.0
        if self.use_fill:
            ops = compress_runs(self.items[start_index:])
            # ops 의 블록 수 누적으로 원래 인덱스를 추적
            cursor = start_index
            pending: list[str] = []
            pending_blocks = 0
            t_next = time.time()
            for kind, args in ops:
                if self.stop_event.is_set():
                    break
                while self.should_pause() and not self.stop_event.is_set():
                    time.sleep(0.5)
                if kind == "fill":
                    x, y, z, x1, block, cnt = args
                    pending.append(f"fill {self.ox + x} {self.oy + y} {self.oz + z} {self.ox + x1} {self.oy + y} {self.oz + z} {block}")
                else:
                    x, y, z, block, cnt = args
                    pending.append(self._cmd_set(x, y, z, block))
                pending_blocks += cnt
                if pending_blocks >= max(1, int(self.bps * tick)) or len(pending) >= self.batch:
                    self._send(pending)
                    cursor += pending_blocks
                    self.stats.placed += pending_blocks
                    pending, pending_blocks = [], 0
                    t_next += tick
                    time.sleep(max(0.0, t_next - time.time()))
                    if self.on_progress and time.time() - last_report > 1:
                        self.on_progress(cursor, n); last_report = time.time()
            if pending:
                self._send(pending)
                cursor += pending_blocks
                self.stats.placed += pending_blocks
            i = cursor
        else:
            t_next = time.time()
            while i < n:
                if self.stop_event.is_set():
                    break
                while self.should_pause() and not self.stop_event.is_set():
                    time.sleep(0.5)
                    t_next = time.time()
                rate = self.bps * speed_at(self.profile, i / max(1, n))
                per_tick = max(1, int(round(rate * tick)))
                end = min(n, i + per_tick, i + self.batch)
                # 단계 경계에서 끊어 잠시 멈춘다 (버스트 연출)
                for g in self.groups:
                    if i < g < end:
                        end = g
                        break
                cmds = [self._cmd_set(*pos, v.block) for pos, v in self.items[i:end]]
                self._send(cmds)
                count = end - i
                self.stats.placed += count
                i = end
                if self.group_pause and i in self.groups:
                    time.sleep(self.group_pause)
                    t_next = time.time()
                t_next += count / rate
                time.sleep(max(0.0, t_next - time.time()))
                if self.on_progress and time.time() - last_report > 1:
                    self.on_progress(i, n); last_report = time.time()
        if self.on_progress:
            self.on_progress(i, n)
        if i >= n:
            self.stats.finished = time.time()
        return i

    def _send(self, cmds: list[str]) -> None:
        for attempt in range(3):
            try:
                self.r.commands(cmds)
                self.stats.commands += len(cmds)
                return
            except RconError:
                self.stats.errors += 1
                if attempt == 2:
                    raise
                time.sleep(1 + attempt)
                self.r.close()
                self.r.connect()

    # ---------------- 검증과 수정 ----------------
    def verify(self, sample: float = 1.0, fix: bool = True, seed: int = 0) -> BuildStats:
        """execute if block 으로 배치 결과를 확인하고 다른 블록은 다시 놓는다."""
        rng = random.Random(seed)
        targets = [it for it in self.items if sample >= 1.0 or rng.random() < sample]
        for k in range(0, len(targets), self.batch):
            chunk = targets[k:k + self.batch]
            cmds = [f"execute if block {self.ox + x} {self.oy + y} {self.oz + z} {v.block}" for (x, y, z), v in chunk]
            res = self.r.commands(cmds)
            redo = []
            for ((pos, v), out) in zip(chunk, res):
                self.stats.verified += 1
                if "passed" not in out.lower():
                    self.stats.failed_checks.append((pos, v.block, out[:80]))
                    redo.append(self._cmd_set(*pos, v.block))
            if fix and redo:
                self.r.commands(redo)
                self.stats.fixed += len(redo)
        return self.stats
