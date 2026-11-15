"""Windows 에이전트(로컬 PC)와 서버를 잇는 간단한 폴링 기반 RPC 허브.

서버(stage_capture)가 push_command() 로 "할 일"을 큐에 넣고 wait_result() 로 블록하면,
에이전트가 /api/agent/poll 로 가져가서 실행한 뒤 /api/agent/ack 로 결과를 돌려준다.
이렇게 하면 stage_capture 의 기존 동기 코드 구조를 거의 그대로 쓸 수 있다.
"""
from __future__ import annotations

import threading
import time
import uuid


class AgentHub:
    def __init__(self):
        self._lock = threading.Lock()
        self._last_heartbeat = 0.0
        self._pending: list[dict] = []
        self._results: dict[str, dict] = {}
        self.last_ip: str | None = None     # 진단용: 마지막으로 heartbeat/poll 을 보낸 IP
        self.seen_ips: dict[str, float] = {}  # 진단용: 지금까지 본 모든 IP와 마지막 접속 시각
                                              # (여러 IP가 섞여 보이면 중복 실행된 에이전트가 있다는 뜻)

    def is_online(self, timeout: float = 25.0) -> bool:
        return (time.time() - self._last_heartbeat) < timeout

    def _note_ip(self, ip: str | None) -> None:
        if not ip:
            return
        self.last_ip = ip
        self.seen_ips[ip] = time.time()

    def heartbeat(self, ip: str | None = None) -> None:
        with self._lock:
            self._last_heartbeat = time.time()
            self._note_ip(ip)

    def push_command(self, cmd: dict) -> str:
        cid = uuid.uuid4().hex
        cmd = {**cmd, "id": cid}
        with self._lock:
            self._pending.append(cmd)
        return cid

    def drain(self, ip: str | None = None) -> list[dict]:
        with self._lock:
            self._note_ip(ip)
            items, self._pending = self._pending, []
            return items

    def ack(self, cid: str, result: dict, ip: str | None = None) -> None:
        with self._lock:
            self._note_ip(ip)
            self._results[cid] = result

    def wait_result(self, cid: str, timeout: float = 60.0) -> dict:
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if cid in self._results:
                    return self._results.pop(cid)
            time.sleep(0.2)
        raise TimeoutError(f"에이전트 응답 시간 초과 (명령 {cid})")


HUB = AgentHub()
