"""Minecraft RCON 프로토콜 클라이언트 (Source RCON). 스레드마다 별도 연결을 쓴다."""
from __future__ import annotations

import socket
import struct
import threading
import time


class RconError(RuntimeError):
    pass


class Rcon:
    AUTH, EXEC, RESPONSE, AUTH_RESPONSE = 3, 2, 0, 2

    def __init__(self, host: str, port: int, password: str, timeout: float = 10.0):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout
        self.sock: socket.socket | None = None
        self._req = 0
        self._lock = threading.RLock()  # commands()가 실패 시 command()로 재진입 호출하므로 재진입 가능해야 함

    def connect(self) -> "Rcon":
        if not self.password:
            raise RconError("RCON 비밀번호가 설정되지 않았습니다 (config.yaml server.rcon_password)")
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except OSError as e:
            raise RconError(f"RCON 연결 실패 {self.host}:{self.port}: {e}") from e
        self.sock.settimeout(self.timeout)
        rid = self._send(self.AUTH, self.password)
        while True:
            pid, ptype, _ = self._recv()
            if ptype == self.AUTH_RESPONSE:
                if pid == -1:
                    raise RconError("RCON 인증 실패 (비밀번호 확인)")
                if pid == rid:
                    return self

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *a):
        self.close()

    def _send(self, ptype: int, body: str) -> int:
        self._req = (self._req + 1) % 2_000_000_000
        data = body.encode("utf-8")
        pkt = struct.pack("<ii", self._req, ptype) + data + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(pkt)) + pkt)
        return self._req

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("RCON 연결이 끊어졌습니다")
            buf += chunk
        return buf

    def _recv(self) -> tuple[int, int, str]:
        (length,) = struct.unpack("<i", self._recv_exact(4))
        data = self._recv_exact(length)
        pid, ptype = struct.unpack("<ii", data[:8])
        return pid, ptype, data[8:-2].decode("utf-8", errors="replace")

    def command(self, cmd: str) -> str:
        """명령 실행. 연결이 끊어져 있으면 한 번 재연결해서 재시도한다 (예: RCON 유휴 타임아웃으로
        끊긴 뒤 player_online() 같은 폴링 루프가 장시간 반복 호출하는 경우)."""
        with self._lock:
            try:
                return self._command_once(cmd)
            except (OSError, struct.error, RconError):
                self.close()
                self.connect()
                return self._command_once(cmd)

    def _command_once(self, cmd: str) -> str:
        if not self.sock:
            self.connect()
        try:
            rid = self._send(self.EXEC, cmd)
            marker = self._send(self.RESPONSE, "")
            out = []
            while True:
                pid, ptype, body = self._recv()
                if pid == marker:
                    # 바닐라 서버는 잘못된 패킷 유형에 'Unknown request' 를 돌려준다
                    break
                if pid == rid:
                    out.append(body)
            return "".join(out)
        except (OSError, struct.error) as e:
            self.close()
            raise RconError(f"RCON 명령 실패: {e}") from e

    def commands(self, cmds: list[str]) -> list[str]:
        """여러 명령을 파이프라인으로 보내 왕복 지연을 줄인다."""
        with self._lock:
            if not self.sock:
                self.connect()
            try:
                ids = [self._send(self.EXEC, c) for c in cmds]
                marker = self._send(self.RESPONSE, "")
                res: dict[int, list[str]] = {i: [] for i in ids}
                while True:
                    pid, _, body = self._recv()
                    if pid == marker:
                        break
                    if pid in res:
                        res[pid].append(body)
                return ["".join(res[i]) for i in ids]
            except (OSError, struct.error, RconError):
                self.close()
                # 일부 RCON 서버 구현은 한 번에 여러 명령을 파이프라인으로 보내면 연결을 끊는다.
                # 그런 경우 재연결 후 한 개씩 순차 전송으로 대체한다 (느리지만 훨씬 안정적).
                self.connect()
                return [self.command(c) for c in cmds]

    def ping(self) -> bool:
        try:
            self.command("list")
            return True
        except RconError:
            return False


def wait_for_rcon(host: str, port: int, password: str, timeout: float) -> Rcon:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            return Rcon(host, port, password).connect()
        except RconError as e:
            last = e
            time.sleep(2)
    raise RconError(f"{timeout:.0f}초 안에 RCON 에 연결하지 못했습니다: {last}")
