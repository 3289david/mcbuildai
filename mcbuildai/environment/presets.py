"""그래픽 및 환경: 시간대, 날씨, 바이옴, 파티클, 셰이더/리소스팩 설정, 환경 프리셋."""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from mcbuildai.builder.rcon import Rcon
from mcbuildai.builder.world import set_biome
from mcbuildai.config import Config
from mcbuildai.planner.dna import TIMES, BuildingDNA


@dataclass
class EnvPreset:
    id: str
    ko: str
    time: int
    weather: str              # clear / rain / thunder / snow
    biome: str = "minecraft:plains"
    particles: list[str] = field(default_factory=list)
    grade: str = "neutral"    # 편집 색감: neutral / warm / cool / night / vivid
    haze: float = 0.0         # 편집 안개 강도 0~1


PRESETS: dict[str, EnvPreset] = {p.id: p for p in [
    EnvPreset("day_clear", "맑은 낮", 6000, "clear", grade="vivid"),
    EnvPreset("morning_mist", "안개 낀 아침", 0, "clear", particles=["minecraft:white_ash"], grade="cool", haze=0.35),
    EnvPreset("sunset", "노을", 12000, "clear", grade="warm"),
    EnvPreset("night_lights", "밤 조명", 18000, "clear", particles=["minecraft:end_rod"], grade="night"),
    EnvPreset("rain", "비", 6000, "rain", grade="cool", haze=0.2),
    EnvPreset("storm", "뇌우", 13000, "thunder", grade="night", haze=0.25),
    EnvPreset("snow", "눈", 6000, "rain", biome="minecraft:snowy_plains", particles=["minecraft:snowflake"],
              grade="cool"),
    EnvPreset("cherry", "벚꽃", 1000, "clear", biome="minecraft:cherry_grove", particles=["minecraft:cherry_leaves"],
              grade="warm"),
]}


def preset_for(dna: BuildingDNA) -> EnvPreset:
    """건축물에 맞는 환경 프리셋 적용."""
    if dna.weather == "snow" or dna.terrain == "snow":
        return PRESETS["snow"]
    if dna.palette == "cherry_blossom" or dna.event == "spring_blossom":
        return PRESETS["cherry"]
    if dna.weather == "thunder":
        return PRESETS["storm"]
    if dna.weather == "rain":
        return PRESETS["rain"]
    return {"morning": PRESETS["morning_mist"], "noon": PRESETS["day_clear"], "sunset": PRESETS["sunset"],
            "night": PRESETS["night_lights"]}.get(dna.time_of_day, PRESETS["day_clear"])


def multi_env_set(primary: EnvPreset) -> list[EnvPreset]:
    """멀티 환경 렌더링: 같은 건축물을 낮/밤/비/눈 등으로 촬영할 목록."""
    ids = ["day_clear", "sunset", "night_lights", "rain", "snow"]
    return [PRESETS[i] for i in ids if i != primary.id][:3]


def apply(r: Rcon, preset: EnvPreset, lo, hi) -> None:
    w = {"clear": "clear", "rain": "rain", "thunder": "thunder", "snow": "rain"}[preset.weather]
    r.commands([f"time set {preset.time}", f"weather {w} 1000000"])
    set_biome(r, lo, hi, preset.biome)


def day_night_transition(r: Rcon, start: int, end: int, seconds: float, stop: threading.Event | None = None) -> None:
    """낮과 밤 전환: seconds 동안 time 을 start 에서 end 까지 부드럽게 옮긴다."""
    steps = max(1, int(seconds * 10))
    span = (end - start) % 24000
    for i in range(steps + 1):
        if stop and stop.is_set():
            return
        r.command(f"time set {int(start + span * i / steps) % 24000}")
        time.sleep(seconds / steps)


class ParticleLoop:
    """파티클 효과: 건축물 주변에 주기적으로 파티클을 뿌린다."""

    def __init__(self, r: Rcon, preset: EnvPreset, center, radius: float, height: float):
        self.r, self.preset, self.c, self.rad, self.h = r, preset, center, radius, height
        self.stop = threading.Event()
        self.th: threading.Thread | None = None

    def _run(self):
        cx, cy, cz = self.c
        while not self.stop.is_set():
            for p in self.preset.particles:
                self.r.command(f"particle {p} {cx:.1f} {cy + self.h / 2:.1f} {cz:.1f} "
                               f"{self.rad:.1f} {self.h / 2:.1f} {self.rad:.1f} 0.01 200 force")
            time.sleep(0.5)

    def __enter__(self):
        if self.preset.particles:
            self.th = threading.Thread(target=self._run, daemon=True)
            self.th.start()
        return self

    def __exit__(self, *a):
        self.stop.set()
        if self.th:
            self.th.join(timeout=3)


# ---------------------------------------------------------------------------
# 클라이언트 셰이더 / 리소스팩 (Iris 설정 파일, options.txt)
# ---------------------------------------------------------------------------
def client_graphics(cfg: Config, shader_pack: str | None, resource_packs: list[str] | None) -> bool:
    """클라이언트 설정 파일을 바꾼다. 바뀐 내용이 있으면 True (클라이언트 재시작 필요)."""
    gd = cfg.get("client.game_dir")
    if not gd:
        return False
    game_dir = Path(gd)
    changed = False
    if shader_pack is not None:
        iris = game_dir / "config" / "iris.properties"
        props = {}
        if iris.exists():
            for line in iris.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.split("=", 1)
                    props[k.strip()] = v.strip()
        want = {"shaderPack": shader_pack, "enableShaders": "true" if shader_pack else "false"}
        if any(props.get(k) != v for k, v in want.items()):
            props.update(want)
            iris.parent.mkdir(parents=True, exist_ok=True)
            iris.write_text("\n".join(f"{k}={v}" for k, v in props.items()) + "\n", encoding="utf-8")
            changed = True
    if resource_packs is not None:
        opt = game_dir / "options.txt"
        lines = opt.read_text(encoding="utf-8").splitlines() if opt.exists() else []
        packs = ["vanilla"] + [f"file/{p}" for p in resource_packs]
        new_line = "resourcePacks:" + json.dumps(packs)
        out, found = [], False
        for line in lines:
            if line.startswith("resourcePacks:"):
                found = True
                if line != new_line:
                    changed = True
                out.append(new_line)
            else:
                out.append(line)
        if not found:
            out.append(new_line)
            changed = True
        opt.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def available_shaderpacks(cfg: Config) -> list[str]:
    gd = cfg.get("client.game_dir")
    d = Path(gd) / "shaderpacks" if gd else None
    return sorted(p.name for p in d.iterdir()) if d and d.exists() else []


def available_resourcepacks(cfg: Config) -> list[str]:
    gd = cfg.get("client.game_dir")
    d = Path(gd) / "resourcepacks" if gd else None
    return sorted(p.name for p in d.iterdir()) if d and d.exists() else []
