"""Minecraft 클라이언트 실행과 화면 정리 (OS 별 처리).

권장: Prism Launcher 인스턴스(Fabric + Sodium + Iris) 를 만들고
  prismlauncher --launch <인스턴스> --server 127.0.0.1:25565
명령을 client.launch_command 에 넣는다.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import time

from mcbuildai.config import IS_WINDOWS, Config


class ClientManager:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None

    def launch(self) -> None:
        cmd = self.cfg.get("client.launch_command")
        if not cmd:
            raise RuntimeError("client.launch_command 가 비어 있습니다 (Prism Launcher 실행 명령 설정 필요)")
        args = cmd if IS_WINDOWS else shlex.split(cmd)
        self.proc = subprocess.Popen(args, shell=IS_WINDOWS)

    def wait_until_online(self, is_online, timeout: float) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if is_online():
                return
            time.sleep(3)
        raise TimeoutError("촬영용 플레이어가 서버에 접속하지 않았습니다")

    def press_key(self, key: str) -> None:
        """Minecraft 창을 앞으로 가져와 키를 누른다 (F1: HUD 숨김)."""
        title = self.cfg.get("client.window_title")
        if IS_WINDOWS:
            ps = (f"$w=New-Object -ComObject WScript.Shell; if($w.AppActivate('{title}')){{Start-Sleep -Milliseconds 300;"
                  f"$w.SendKeys('{{{key}}}')}}")
            subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=False, timeout=15)
        else:
            if not shutil.which("xdotool"):
                raise RuntimeError("Linux 에서 키 입력 자동화에 xdotool 이 필요합니다")
            subprocess.run(["xdotool", "search", "--name", title, "windowactivate", "--sync", "key", key],
                           check=False, timeout=15)

    def hide_hud(self) -> None:
        self.press_key("F1")

    def close(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
