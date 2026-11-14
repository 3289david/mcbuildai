"""FFmpeg/ffprobe 공용 함수."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from mcbuildai.config import IS_WINDOWS, Config


class FFError(RuntimeError):
    pass


def bins(cfg: Config | None = None) -> tuple[str, str]:
    ff = (cfg.get("paths.ffmpeg") if cfg else None) or shutil.which("ffmpeg")
    fp = (cfg.get("paths.ffprobe") if cfg else None) or shutil.which("ffprobe")
    if not ff or not fp:
        raise FFError("ffmpeg/ffprobe 를 찾을 수 없습니다")
    return ff, fp


def run(args: list[str], cfg: Config | None = None, timeout: float | None = None) -> None:
    ff, _ = bins(cfg)
    p = subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise FFError(p.stderr.decode(errors="replace")[-2000:])


def probe(path: Path, cfg: Config | None = None) -> dict:
    _, fp = bins(cfg)
    p = subprocess.run([fp, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True)
    if p.returncode != 0:
        raise FFError(p.stderr.decode(errors="replace"))
    return json.loads(p.stdout)


def duration(path: Path, cfg: Config | None = None) -> float:
    return float(probe(path, cfg)["format"]["duration"])


def video_size(path: Path, cfg: Config | None = None) -> tuple[int, int]:
    for s in probe(path, cfg)["streams"]:
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise FFError(f"영상 스트림 없음: {path}")


def has_audio(path: Path, cfg: Config | None = None) -> bool:
    return any(s.get("codec_type") == "audio" for s in probe(path, cfg)["streams"])


def filter_path(p: str | Path) -> str:
    """필터 문자열 안에 넣을 경로 이스케이프 (Windows 드라이브 콜론 포함)."""
    s = str(Path(p).resolve()).replace("\\", "/")
    return s.replace(":", "\\:").replace("'", "\\'")


def default_font(cfg: Config) -> str:
    f = cfg.get("paths.font_file")
    if f and Path(f).exists():
        return f
    cands = (["C:/Windows/Fonts/malgunbd.ttf", "C:/Windows/Fonts/malgun.ttf"] if IS_WINDOWS else
             ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
              "/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"])
    for c in cands:
        if os.path.exists(c):
            return c
    raise FFError("한글 폰트를 찾을 수 없습니다. paths.font_file 을 지정하세요.")


def extract_frame(src: Path, t: float, out: Path, cfg: Config | None = None) -> Path:
    run(["-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "2", str(out)], cfg)
    return out


def loudnorm_two_pass(src: Path, out: Path, target: float = -14.0, cfg: Config | None = None) -> Path:
    """2단계 loudnorm: 측정 후 보정 (음량 자동 조절)."""
    import re as _re
    ffb, _ = bins(cfg)
    p = subprocess.run([ffb, "-hide_banner", "-i", str(src), "-af",
                        f"loudnorm=I={target}:TP=-1.5:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = _re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", p.stderr, flags=_re.S)
    if not m:
        raise FFError("loudnorm 측정 실패: " + p.stderr[-500:])
    j = json.loads(m.group(0))
    af = (f"loudnorm=I={target}:TP=-1.5:LRA=11:measured_I={j['input_i']}:measured_TP={j['input_tp']}:"
          f"measured_LRA={j['input_lra']}:measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
    run(["-i", str(src), "-af", af, "-ar", "48000", str(out)], cfg)
    return out
