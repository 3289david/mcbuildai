"""FFmpeg 화면 녹화. Windows=gdigrab, Linux=x11grab. 녹화 중 1초마다 실시간 미리보기 JPG 를 갱신한다."""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from mcbuildai.config import IS_WINDOWS, Config


def ffmpeg_bin(cfg: Config | None = None) -> str:
    p = (cfg.get("paths.ffmpeg") if cfg else None) or shutil.which("ffmpeg")
    if not p:
        raise FileNotFoundError("ffmpeg 를 찾을 수 없습니다. 설치 후 PATH 에 추가하거나 paths.ffmpeg 를 지정하세요.")
    return p


class ScreenRecorder:
    def __init__(self, cfg: Config, live_preview: Path | None = None):
        self.cfg = cfg
        self.proc: subprocess.Popen | None = None
        self.out: Path | None = None
        self.live_preview = live_preview
        self.started_at = 0.0

    def _input_args(self) -> list[str]:
        c = self.cfg
        fps = str(c.get("capture.fps"))
        w, h = int(c.get("capture.width")), int(c.get("capture.height"))
        if IS_WINDOWS:
            mode = c.get("capture.windows_source", "desktop")
            args = ["-f", "gdigrab", "-framerate", fps, "-draw_mouse", "0"]
            if mode == "window":
                return args + ["-i", f"title={c.get('client.window_title')}"]
            return args + ["-offset_x", str(c.get("capture.offset_x")), "-offset_y", str(c.get("capture.offset_y")),
                           "-video_size", f"{w}x{h}", "-i", "desktop"]
        disp = c.get("capture.display")
        return ["-f", "x11grab", "-framerate", fps, "-draw_mouse", "0", "-video_size", f"{w}x{h}",
                "-i", f"{disp}+{c.get('capture.offset_x')},{c.get('capture.offset_y')}"]

    def start(self, out: Path) -> None:
        if self.proc:
            raise RuntimeError("이미 녹화 중입니다")
        out.parent.mkdir(parents=True, exist_ok=True)
        enc = self.cfg.get("capture.encoder")
        enc_args = ["-c:v", enc]
        if enc == "libx264":
            enc_args += ["-preset", "ultrafast", "-crf", str(self.cfg.get("capture.crf"))]
        elif "nvenc" in enc:
            enc_args += ["-preset", "p4", "-cq", str(self.cfg.get("capture.crf"))]
        cmd = [ffmpeg_bin(self.cfg), "-hide_banner", "-loglevel", "error", "-y", *self._input_args(),
               "-map", "0:v", *enc_args, "-pix_fmt", "yuv420p", str(out)]
        if self.live_preview:
            cmd += ["-map", "0:v", "-vf", "fps=1,scale=480:-2", "-update", "1", "-q:v", "6", str(self.live_preview)]
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.out = out
        self.started_at = time.time()
        time.sleep(1.0)
        if self.proc.poll() is not None:
            err = self.proc.stderr.read().decode(errors="replace")
            self.proc = None
            raise RuntimeError(f"녹화 시작 실패: {err[:500]}")

    def stop(self) -> Path:
        if not self.proc:
            raise RuntimeError("녹화 중이 아닙니다")
        try:
            self.proc.stdin.write(b"q")
            self.proc.stdin.flush()
            self.proc.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        err = self.proc.stderr.read().decode(errors="replace") if self.proc.stderr else ""
        self.proc = None
        if not self.out or not self.out.exists() or self.out.stat().st_size < 1000:
            raise RuntimeError(f"녹화 파일이 비어 있습니다: {err[:500]}")
        meta = {"started_at": self.started_at, "wall_seconds": time.time() - self.started_at}
        self.out.with_suffix(".json").write_text(json.dumps(meta), encoding="utf-8")
        return self.out


def snapshot(cfg: Config, out: Path) -> Path:
    """단일 프레임 캡처 (촬영 화면 확인용)."""
    rec = ScreenRecorder(cfg)
    cmd = [ffmpeg_bin(cfg), "-hide_banner", "-loglevel", "error", "-y", *rec._input_args(), "-frames:v", "1", str(out)]
    subprocess.run(cmd, check=True, timeout=30)
    return out
