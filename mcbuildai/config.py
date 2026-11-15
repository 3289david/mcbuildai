"""설정 로딩. config.yaml 을 읽고 기본값과 병합한다."""
from __future__ import annotations

import copy
import os
import platform
from pathlib import Path
from typing import Any

import yaml

IS_WINDOWS = platform.system() == "Windows"

DEFAULTS: dict[str, Any] = {
    "paths": {
        "data_dir": "data",            # DB, 로그, 체크포인트
        "output_dir": "output",        # 영상 결과물
        "schematic_dir": "output/schematics",
        "music_dir": "assets/music",   # 사용자가 넣는 음원 (라이선스 파일 필요)
        "sfx_dir": "assets/sfx",       # 사용자가 넣는 효과음
        "font_file": "",               # 비어 있으면 OS 기본 한글 폰트 탐색
        "ffmpeg": "",                  # 비어 있으면 PATH 에서 찾음
        "ffprobe": "",
    },
    "content": {
        "language": "en",              # 영상 자막/제목/설명 언어: en / ko
        "localize_ko": True,           # YouTube 메타데이터에 한국어 번역도 함께 등록
    },
    "generator": {
        "engine": "genmc",             # genmc(항상 genMC, 실패 시 중단) / auto(실패 시 내장 생성기로 대체) / procedural
    },
    "genmc": {
        "mcp_url": "https://genmc.app/api/mcp",
        "export_version": "1.21.1",    # 서버 Minecraft 버전과 맞춘다
        # genMC 는 MCP 표준 서버라 특정 LLM에 종속되지 않는다 (Claude/ChatGPT/Codex 모두 지원).
        # provider 를 비워두면 글로벌 ai.provider(기본 OpenRouter)를 그대로 써서 같은 키로 운전한다.
        # provider: anthropic 으로 명시하면 아래 두 값으로 전용 Anthropic 클라이언트를 쓴다.
        "provider": None,
        "anthropic_api_key_env": "ANTHROPIC_API_KEY",
        "model": None,                 # None이면 provider에 맞는 기본 모델을 코드에서 고른다
    },
    "planner": {
        "horizon_days": 7,
        # 설날/추석 당일(양력). 실제 달력과 확인 후 필요하면 수정
        "lunar_dates": {
            "seollal": ["2026-02-17", "2027-02-07", "2028-01-27", "2029-02-13", "2030-02-03"],
            "chuseok": ["2026-09-25", "2027-09-15", "2028-10-03", "2029-09-22", "2030-09-12"],
        },
    },
    "camera": {
        "mode": "display",             # display(보간 카메라 리그) / tp(플레이어 직접 이동)
        "auto_hide_hud": True,         # 접속 후 F1 키로 HUD 숨김
        "multi_env": True,             # 낮/밤/비/눈 추가 촬영
    },
    "audio": {
        "allowed_licenses": ["CC0", "PDM", "CC-BY", "YouTube Audio Library", "Owned", "Self-made",
                             "AI-generated-owned"],
        "auto_fetch": True,            # 음원이 부족하면 자동 수집
        "min_tracks_per_mood": 3,
        "min_sfx_per_kind": 3,
        "music_min_sec": 60,
        "music_max_sec": 420,
        "sources": ["openverse", "jamendo", "freesound"],
        "jamendo_client_id_env": "JAMENDO_CLIENT_ID",
        "freesound_token_env": "FREESOUND_API_KEY",
    },
    "server": {
        "dir": "server",               # Paper 서버 폴더 (paper.jar 위치)
        "jar": "paper.jar",
        "java": "java",
        "memory": "4G",
        "host": "127.0.0.1",
        "rcon_port": 25575,
        "rcon_password": "",           # 반드시 설정
        "world_name": "buildworld",
        "start_timeout_sec": 180,
        "backup_dir": "data/backups",
        "keep_backups": 5,
    },
    "client": {
        # Minecraft 클라이언트 실행 명령. Prism Launcher 권장.
        # 예) prismlauncher -l BuildCam -s 127.0.0.1:25565
        "launch_command": "",
        "camera_player": "",           # 촬영용 계정 닉네임 (spectator 모드로 전환됨)
        "window_title": "Minecraft",   # Windows gdigrab 대상 창 제목
        "game_dir": "",                # 클라이언트 .minecraft 경로 (셰이더/리소스팩 설정용)
        "startup_wait_sec": 60,
    },
    "capture": {
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "display": ":0.0",             # Linux x11grab 디스플레이
        "offset_x": 0,
        "offset_y": 0,
        "windows_source": "desktop",   # desktop(화면 영역) / window(창 제목으로 캡처)
        "encoder": "libx264",          # NVIDIA 사용 시 h264_nvenc
        "crf": 18,
    },
    "build": {
        "origin": [0, 64, 0],          # 건축 원점
        "blocks_per_second": 400,      # 기본 건축 속도
        "min_blocks": 50000,           # 건축물 기본 최소 블록 수 목표
        "max_commands_per_batch": 200,
        "verify": True,
        "verify_sample": 0.25,         # 검증할 블록 비율
        "min_build_sec": 60,           # 실제 건축(촬영) 시간 범위
        "max_build_sec": 1800,
        "plot_spacing": 200,           # 건축물 간 간격 (위치 자동 선정)
    },
    "video": {
        "short_max_sec": 58,
        "edited_width": 1080,
        "edited_height": 1920,
        "raw_keep_aspect": True,
        "preset": "veryfast",
        "crf": 20,
        "music_volume": 0.6,
        "sfx_volume": 0.9,
        "target_lufs": -14,
        "raw_build_sec": 90,           # 원본 버전의 타임랩스 구간 길이
        "beat_sync": True,             # 장면 길이를 음악 박자에 맞춤
        "block_counter": True,         # 건축 중 블록 수 카운터와 진행 바
        "speed_label": True,           # "240x SPEED" 표시
        "hook_text": True,             # 시작 훅 문구
        "cta_text": True,              # 끝 댓글 유도 문구
        "motion_blur": True,           # 고속 구간 모션 블러
        "loop_end": True,              # 마지막이 첫 장면으로 이어지는 루프 엔딩
        "landscape_edit": True,        # 16:9 가로 편집본(edited_landscape.mp4)도 생성
        "landscape_max_sec": 90,
        "cleanup_tmp": True,
    },
    "ai": {
        "provider": "anthropic",
        "model": "claude-sonnet-5-5",
        "api_key_env": "ANTHROPIC_API_KEY",
    },
    "music_api": {
        # 외부 AI 음악 생성 API (선택). 비우면 로컬 음원 라이브러리만 사용.
        "endpoint": "",
        "api_key_env": "MUSIC_API_KEY",
    },
    "youtube": {
        "client_secrets": "data/client_secret.json",
        "token_file": "data/youtube_token.json",
        "privacy": "private",          # private / unlisted / public
        "category_id": "20",           # Gaming
        "default_language": "en",
        "api_key_env": "YOUTUBE_API_KEY",  # 트렌드 검색용 (없으면 OAuth 사용)
        "trend_queries": ["minecraft build timelapse", "minecraft building shorts"],
        "upload_landscape": False,     # 가로 편집본도 일반 영상으로 업로드
        "schedule_times": ["18:00"],   # 예약 업로드 시각 (현지 시간)
        "timezone": "Asia/Seoul",
        "auto_upload": False,
    },
    "jobs": {
        "daily_count": 1,
        "max_retries": 3,
        "workers": 1,
        "min_free_disk_gb": 20,
        "cleanup_days": 30,
        "daily_time": "09:00",         # 매일 작업 생성 시각
        "experiment_every": 5,         # N 번째 작업마다 A/B 편집 실험
        "online_backlog_target": 8,    # 촬영 에이전트가 켜져 있을 때 이 개수까지 미리 찍어서 쌓아둔다
    },
    "notify": {
        "discord_webhook": "",
    },
    "dashboard": {
        "host": "127.0.0.1",
        "port": 8765,
        "password": None,          # 로그인 비밀번호 (config.yaml 에서 설정 필수)
        "session_secret": None,    # 로그인 쿠키 서명용 비밀키 (config.yaml 에서 설정 필수)
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict, path: Path | None, root: Path):
        self.data = data
        self.path = path
        self.root = root

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def path_of(self, dotted: str) -> Path:
        p = Path(str(self.get(dotted)))
        return p if p.is_absolute() else (self.root / p)

    def save(self) -> None:
        if not self.path:
            raise RuntimeError("설정 파일 경로가 없습니다")
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)


def load_config(path: str | os.PathLike | None = None) -> Config:
    p = Path(path) if path else Path(os.environ.get("MCBUILDAI_CONFIG", "config.yaml"))
    data: dict = {}
    if p.exists():
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    root = p.resolve().parent if p.exists() else Path.cwd()
    cfg = Config(_merge(DEFAULTS, data), p if p.exists() else None, root)
    for key in ("paths.data_dir", "paths.output_dir", "paths.schematic_dir"):
        cfg.path_of(key).mkdir(parents=True, exist_ok=True)
    return cfg
