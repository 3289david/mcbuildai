"""카메라 제어: 촬영용 플레이어를 관전 모드로 만들고 경로를 따라 움직인다.

기본(display) 모드: item_display 엔티티를 카메라 리그로 소환하고 teleport_duration 보간을 켠 뒤
플레이어가 그 엔티티를 spectate 하게 한다. 서버가 틱마다 리그를 옮기면 클라이언트가 위치와 회전을
부드럽게 보간하므로 플레이어를 직접 tp 하는 방식보다 끊김이 적다 (Java 1.20.2 이상).
tp 모드: 플레이어를 직접 tp 한다 (보간 없음, 호환용).
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable

from mcbuildai.builder.rcon import Rcon
from .paths import PathFn

CAM_TAG = "mcb_cam"


class CameraController:
    def __init__(self, rcon: Rcon, player: str, mode: str = "display", hz: int = 20,
                 interpolation_ticks: int = 2):
        if not player:
            raise ValueError("촬영용 플레이어 이름(client.camera_player)이 필요합니다")
        self.r = rcon
        self.player = player
        self.mode = mode
        self.hz = hz
        self.interp = interpolation_ticks
        self.keyframes: list[dict] = []

    def player_online(self) -> bool:
        out = self.r.command("list")
        names = out.split(":", 1)[1] if ":" in out else ""
        return self.player.lower() in [n.strip().lower() for n in names.split(",")]

    def setup(self, start: tuple[float, float, float, float, float]) -> None:
        x, y, z, yaw, pitch = start
        self.r.command(f"gamemode spectator {self.player}")
        self.r.command(f"tp {self.player} {x:.3f} {y:.3f} {z:.3f} {yaw:.2f} {pitch:.2f}")
        if self.mode == "display":
            self.r.command(f"kill @e[tag={CAM_TAG}]")
            self.r.command(f"summon minecraft:item_display {x:.3f} {y:.3f} {z:.3f} "
                           f"{{Tags:[\"{CAM_TAG}\"],teleport_duration:{self.interp},Rotation:[{yaw:.2f}f,{pitch:.2f}f]}}")
            time.sleep(0.3)
            self.r.command(f"spectate @e[tag={CAM_TAG},limit=1] {self.player}")

    def move(self, x, y, z, yaw, pitch) -> None:
        if self.mode == "display":
            self.r.command(f"tp @e[tag={CAM_TAG},limit=1] {x:.3f} {y:.3f} {z:.3f} {yaw:.2f} {pitch:.2f}")
        else:
            self.r.command(f"tp {self.player} {x:.3f} {y:.3f} {z:.3f} {yaw:.2f} {pitch:.2f}")

    def run(self, path: PathFn, duration: float, progress: Callable[[], float] | None = None,
            stop: threading.Event | None = None, label: str = "") -> None:
        """duration 초 동안 경로를 따라 이동. duration<=0 이면 stop 이벤트까지 (건축 중 추적)."""
        dt = 1.0 / self.hz
        t0 = time.time()
        nxt = t0
        while True:
            el = time.time() - t0
            if duration > 0 and el >= duration:
                break
            if stop is not None and stop.is_set():
                break
            p = progress() if progress else 0.0
            if duration > 0:
                t = el / duration
            else:
                # 기간이 정해지지 않은 구간(건축 중)은 건축 진행률을 시간축으로 쓴다
                t = p
            pos = path(min(1.0, t), p)
            self.move(*pos)
            if len(self.keyframes) < 200000 and int(el * self.hz) % self.hz == 0:
                self.keyframes.append({"label": label, "t": round(el, 2), "pos": [round(v, 3) for v in pos]})
            nxt += dt
            time.sleep(max(0.0, nxt - time.time()))

    def hold(self, path: PathFn, seconds: float, label: str = "") -> None:
        self.run(path, seconds, label=label)

    def save_keyframes(self, path: Path) -> None:
        """촬영 위치 자동 저장."""
        Path(path).write_text(json.dumps(self.keyframes, ensure_ascii=False), encoding="utf-8")

    def teardown(self) -> None:
        # 리그 엔티티가 사라지면 관전이 자동으로 풀린다
        self.r.command(f"kill @e[tag={CAM_TAG}]")
