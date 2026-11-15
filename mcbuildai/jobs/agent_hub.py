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

    def is_online(self, timeout: float = 25.0) -> bool:
        return (time.time() - self._last_heartbeat) < timeout

    def heartbeat(self) -> None:
        with self._lock:
            self._last_heartbeat = time.time()

    def push_command(self, cmd: dict) -> str:
        cid = uuid.uuid4().hex
        cmd = {**cmd, "id": cid}
        with self._lock:
            self._pending.append(cmd)
        return cid

    def drain(self) -> list[dict]:
        with self._lock:
            items, self._pending = self._pending, []
            return items

    def ack(self, cid: str, result: dict) -> None:
        with self._lock:
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
