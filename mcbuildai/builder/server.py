"""Paper 서버 프로세스 관리: 설정, 시작/중지/재시작, 충돌 감지, 월드 백업, 빈 월드 생성."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path

from mcbuildai.config import IS_WINDOWS, Config
from mcbuildai.db import DB
from .rcon import Rcon, RconError, wait_for_rcon

# 평지 월드: 기반암 1 + 돌 123 + 흙 3 + 잔디 1 = 128 층 -> 잔디 최상단 y=63, 건축 원점 y=64
FLAT_SETTINGS = {
    "layers": [
        {"block": "minecraft:bedrock", "height": 1},
        {"block": "minecraft:stone", "height": 123},
        {"block": "minecraft:dirt", "height": 3},
        {"block": "minecraft:grass_block", "height": 1},
    ],
    "biome": "minecraft:plains",
    "features": False,
    "lakes": False,
    "structure_overrides": [],
}

GAMERULES = {
    "doDaylightCycle": "false", "doWeatherCycle": "false", "doMobSpawning": "false",
    "doFireTick": "false", "randomTickSpeed": "0", "mobGriefing": "false",
    "sendCommandFeedback": "false", "commandBlockOutput": "false", "logAdminCommands": "false",
    "announceAdvancements": "false", "doTraderSpawning": "false", "doPatrolSpawning": "false",
    "doInsomnia": "false", "spawnRadius": "0", "doTileDrops": "false",
}


class ServerManager:
    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db
        self.dir = cfg.path_of("server.dir")
        self.proc: subprocess.Popen | None = None
        self.log_file = cfg.path_of("paths.data_dir") / "server_console.log"

    # ---------------- 설정 ----------------
    def write_properties(self, new_world: bool = False) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        props_path = self.dir / "server.properties"
        props: dict[str, str] = {}
        if props_path.exists():
            for line in props_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    props[k] = v
        props.update({
            "enable-rcon": "true",
            "rcon.port": str(self.cfg.get("server.rcon_port")),
            "rcon.password": self.cfg.get("server.rcon_password"),
            "level-name": self.cfg.get("server.world_name"),
            "level-type": "minecraft\\:flat",
            "generator-settings": json.dumps(FLAT_SETTINGS, separators=(",", ":")),
            "generate-structures": "false",
            "spawn-monsters": "false",
            "spawn-animals": "false",
            "spawn-npcs": "false",
            "spawn-protection": "0",
            "difficulty": "peaceful",
            "gamemode": "spectator",
            "online-mode": props.get("online-mode", "true"),
            "view-distance": props.get("view-distance", "16"),
            "simulation-distance": "6",
            "max-players": "4",
            "motd": "mcbuildai",
            "broadcast-rcon-to-ops": "false",
        })
        props_path.write_text("\n".join(f"{k}={v}" for k, v in props.items()) + "\n", encoding="utf-8")
        if new_world:
            wd = self.dir / self.cfg.get("server.world_name")
            if wd.exists():
                self.backup(reason="before_new_world")
                shutil.rmtree(wd)

    # ---------------- 프로세스 ----------------
    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def rcon(self) -> Rcon:
        return Rcon(self.cfg.get("server.host"), int(self.cfg.get("server.rcon_port")),
                    self.cfg.get("server.rcon_password")).connect()

    def healthy(self) -> bool:
        try:
            with self.rcon() as r:
                r.command("list")
            return True
        except RconError:
            return False

    def start(self) -> None:
        if self.healthy():
            return
        jar = self.dir / self.cfg.get("server.jar")
        if not jar.exists():
            raise FileNotFoundError(f"서버 jar 를 찾을 수 없습니다: {jar} (Paper 를 내려받아 넣으세요)")
        if not (self.dir / "eula.txt").exists() or "eula=true" not in (self.dir / "eula.txt").read_text():
            raise RuntimeError(f"{self.dir / 'eula.txt'} 에서 Minecraft EULA 동의(eula=true)가 필요합니다")
        self.write_properties()
        mem = self.cfg.get("server.memory")
        cmd = [self.cfg.get("server.java"), f"-Xms{mem}", f"-Xmx{mem}", "-jar", jar.name, "--nogui"]
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if IS_WINDOWS else 0
        logf = open(self.log_file, "ab")
        self.proc = subprocess.Popen(cmd, cwd=self.dir, stdin=subprocess.PIPE, stdout=logf, stderr=subprocess.STDOUT,
                                     creationflags=flags)
        self.db.log("INFO", "server", f"서버 시작 (pid {self.proc.pid})")
        r = wait_for_rcon(self.cfg.get("server.host"), int(self.cfg.get("server.rcon_port")),
                          self.cfg.get("server.rcon_password"), float(self.cfg.get("server.start_timeout_sec")))
        with r:
            self.apply_gamerules(r)

    def apply_gamerules(self, r: Rcon) -> None:
        r.commands([f"gamerule {k} {v}" for k, v in GAMERULES.items()])

    def stop(self, timeout: float = 60) -> None:
        try:
            with self.rcon() as r:
                r.command("save-all flush")
                r.command("stop")
        except RconError:
            pass
        if self.proc:
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
            self.proc = None
        self.db.log("INFO", "server", "서버 중지")

    def restart(self) -> None:
        self.db.log("WARN", "server", "서버 재시작")
        self.stop()
        time.sleep(3)
        self.start()

    def ensure_running(self) -> None:
        """충돌 감지: RCON 응답이 없으면 프로세스를 정리하고 다시 띄운다."""
        if self.healthy():
            return
        if self.proc and self.proc.poll() is not None:
            self.db.log("ERROR", "server", f"서버 프로세스 종료 감지 (코드 {self.proc.returncode})")
        elif self.proc:
            self.db.log("ERROR", "server", "서버 응답 없음, 강제 종료 후 재시작")
            self.proc.kill()
        self.proc = None
        self.start()

    # ---------------- 백업 ----------------
    def backup(self, reason: str = "manual") -> Path | None:
        world = self.dir / self.cfg.get("server.world_name")
        if not world.exists():
            return None
        bdir = self.cfg.path_of("server.backup_dir")
        bdir.mkdir(parents=True, exist_ok=True)
        live = self.healthy()
        if live:
            with self.rcon() as r:
                r.command("save-off")
                r.command("save-all flush")
        try:
            name = f"{world.name}_{datetime.now():%Y%m%d_%H%M%S}_{reason}.zip"
            out = bdir / name
            with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
                for p in world.rglob("*"):
                    if p.is_file() and p.name != "session.lock":
                        z.write(p, p.relative_to(world.parent))
        finally:
            if live:
                with self.rcon() as r:
                    r.command("save-on")
        keep = int(self.cfg.get("server.keep_backups"))
        backups = sorted(bdir.glob(f"{world.name}_*.zip"))
        for old in backups[:-keep] if keep > 0 else []:
            old.unlink(missing_ok=True)
        self.db.log("INFO", "server", f"월드 백업: {out.name}")
        return out
