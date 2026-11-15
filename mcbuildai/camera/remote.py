"""Windows 에이전트(로컬 PC)에게 실행을 위임하는 ClientManager/ScreenRecorder 호환 구현체.

stage_capture() 는 이 클래스들과 로컬 클래스(ClientManager, ScreenRecorder)를 똑같은
인터페이스로 취급하므로, 에이전트가 접속해 있는지 여부로 둘 중 하나를 고르기만 하면 되고
카메라 RCON 연출 로직은 전혀 건드릴 필요가 없다.
"""
from __future__ import annotations

import time
from pathlib import Path

from mcbuildai.config import Config
from mcbuildai.jobs.agent_hub import HUB


class RemoteClientManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def launch(self) -> None:
        cid = HUB.push_command({"type": "launch"})
        HUB.wait_result(cid, timeout=30)

    def wait_until_online(self, is_online, timeout: float) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if is_online():
                return
            time.sleep(3)
        raise TimeoutError("촬영용 플레이어가 서버에 접속하지 않았습니다 (에이전트)")

    def hide_hud(self) -> None:
        cid = HUB.push_command({"type": "hide_hud"})
        HUB.wait_result(cid, timeout=15)

    def close(self) -> None:
        cid = HUB.push_command({"type": "close"})
        try:
            HUB.wait_result(cid, timeout=15)
        except TimeoutError:
            pass


class RemoteScreenRecorder:
    def __init__(self, cfg: Config, work_dir: Path, live_preview=None):
        self.cfg = cfg
        self.work_dir = Path(work_dir)
        self._name: str | None = None

    def start(self, out_path) -> None:
        self._name = Path(out_path).stem
        cid = HUB.push_command({"type": "record_start", "name": self._name})
        HUB.wait_result(cid, timeout=20)

    def stop(self) -> None:
        cid = HUB.push_command({"type": "record_stop", "name": self._name, "work_dir": str(self.work_dir)})
        # 정지 + 파일 업로드까지 기다려야 하니 넉넉하게 잡는다.
        HUB.wait_result(cid, timeout=180)
