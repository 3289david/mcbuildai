"""AI 음향: 음원 라이브러리(라이선스 확인), 자동 분석(BPM/박자/클라이맥스), 분위기별 선곡,
음악-영상 싱크, 박자에 맞춘 효과음, 출처 표기, 믹싱.

음원 라이브러리 규칙
- assets/music/ 아래 음원 파일마다 '파일명.확장자.json' (또는 '파일명.json') 메타데이터가 있어야 한다.
  {"title": "...", "artist": "...", "license": "CC0", "source_url": "...", "moods": ["epic", "calm"]}
- license 가 허용 목록(audio.allowed_licenses)에 없거나 메타데이터가 없는 음원은 쓰지 않는다 (저작권 위험 필터링).
- 자동 수집기(fetcher.py)는 assets/music/auto, assets/sfx/auto 에 메타데이터와 함께 저장한다.
- 효과음 이름 규칙: place_* (블록 설치), whoosh_* (전환), complete_* (완성), riser_* (공개 직전 상승), impact_* (공개 순간).
"""
from __future__ import annotations

import json
import random
import subprocess
import urllib.request
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from mcbuildai.config import Config
from mcbuildai.db import DB
from mcbuildai.editor.ff import bins

AUDIO_EXT = {".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac"}
DEFAULT_ALLOWED = ["CC0", "PDM", "CC-BY", "YouTube Audio Library", "Owned", "Self-made", "AI-generated-owned"]
SR = 44100
ASR = 22050  # 분석용 샘플레이트
ONSET_DELAY = 0.13  # 스펙트럼 플럭스 프레임 시간과 실제 타격 시점의 차이 (테스트 신호로 측정, 오차 약 30ms)

MOOD_BY_STYLE = {
    "medieval": ["epic", "adventure"], "gothic": ["epic", "dark"], "classical": ["epic", "elegant"],
    "japanese": ["calm", "asian"], "chinese": ["asian", "epic"], "korean": ["calm", "asian"],
    "modern": ["upbeat", "chill"], "futuristic": ["electronic", "upbeat"], "fantasy": ["mysterious", "epic"],
    "desert": ["warm", "adventure"], "nordic": ["calm", "adventure"], "steampunk": ["upbeat", "adventure"],
}


@dataclass
class Track:
    path: Path
    title: str
    artist: str
    license: str
    source_url: str
    moods: list[str]
    attribution: str = ""
    license_url: str = ""
    meta_path: Path | None = None
    analysis: dict = field(default_factory=dict)

    def credit(self) -> str:
        if self.attribution:
            return f"Music: {self.attribution}"
        s = f"Music: \"{self.title}\" by {self.artist}"
        if self.license:
            s += f" ({self.license}{' ' + self.license_url if self.license_url else ''})"
        if self.source_url:
            s += f" {self.source_url}"
        return s


def _meta_path(p: Path) -> Path | None:
    for cand in (Path(str(p) + ".json"), p.with_suffix(".json")):
        if cand.exists() and cand != p:
            return cand
    return None


def scan_library(cfg: Config) -> tuple[list[Track], list[tuple[str, str]]]:
    """(사용 가능한 트랙, 제외된 파일과 사유)."""
    d = cfg.path_of("paths.music_dir")
    allowed = [a.lower() for a in cfg.get("audio.allowed_licenses", DEFAULT_ALLOWED)]
    ok, rejected = [], []
    if not d.exists():
        return ok, rejected
    for p in sorted(d.rglob("*")):
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        mp = _meta_path(p)
        if not mp:
            rejected.append((p.name, "라이선스 메타데이터(.json) 없음"))
            continue
        try:
            m = json.loads(mp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rejected.append((p.name, "메타데이터 JSON 오류"))
            continue
        lic = str(m.get("license", ""))
        if lic.lower() not in allowed:
            rejected.append((p.name, f"허용되지 않은 라이선스: {lic or '미기재'}"))
            continue
        ok.append(Track(p, m.get("title", p.stem), m.get("artist", ""), lic, m.get("source_url", ""),
                        [x.lower() for x in m.get("moods", [])], m.get("attribution", ""),
                        m.get("license_url", ""), mp, m.get("analysis") or {}))
    return ok, rejected


def sfx_files(cfg: Config, prefix: str) -> list[Path]:
    """효과음 파일. 메타데이터가 있으면 라이선스를 확인하고, 없으면 사용자가 직접 넣은 파일로 본다."""
    d = cfg.path_of("paths.sfx_dir")
    if not d.exists():
        return []
    allowed = [a.lower() for a in cfg.get("audio.allowed_licenses", DEFAULT_ALLOWED)]
    out = []
    for p in sorted(d.rglob(f"{prefix}*")):
        if p.suffix.lower() not in AUDIO_EXT:
            continue
        mp = _meta_path(p)
        if mp:
            try:
                if str(json.loads(mp.read_text(encoding="utf-8")).get("license", "")).lower() not in allowed:
                    continue
            except json.JSONDecodeError:
                continue
        out.append(p)
    return out


def sfx_credit(p: Path) -> str | None:
    mp = _meta_path(p)
    if not mp:
        return None
    m = json.loads(mp.read_text(encoding="utf-8"))
    return m.get("attribution") or f"\"{m.get('title', p.stem)}\" by {m.get('artist', '')} ({m.get('license', '')})"


# ---------------------------------------------------------------------------
# PCM 처리
# ---------------------------------------------------------------------------
def decode(path: Path, cfg: Config | None = None, sr: int = SR, mono: bool = False) -> np.ndarray:
    ff, _ = bins(cfg)
    ch = 1 if mono else 2
    p = subprocess.run([ff, "-v", "error", "-i", str(path), "-f", "f32le", "-ac", str(ch), "-ar", str(sr), "-"],
                       capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.decode(errors="replace"))
    a = np.frombuffer(p.stdout, dtype=np.float32).copy()
    return a if mono else a.reshape(-1, 2)


def write_wav(path: Path, pcm: np.ndarray) -> Path:
    pcm = np.clip(pcm, -1, 1)
    data = (pcm * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(data)
    return path


def fade(pcm: np.ndarray, fin: float, fout: float) -> np.ndarray:
    out = pcm.copy()
    a, b = min(len(out), int(fin * SR)), min(len(out), int(fout * SR))
    if a:
        out[:a] *= np.linspace(0, 1, a)[:, None]
    if b:
        out[-b:] *= np.linspace(1, 0, b)[:, None]
    return out


def onset_envelope(mono: np.ndarray, sr: int = ASR, hop: int = 512) -> np.ndarray:
    """스펙트럼 플럭스 기반 온셋 강도 (hop 마다 1 값)."""
    n = (len(mono) - 2048) // hop
    if n <= 4:
        return np.zeros(1)
    win = np.hanning(2048).astype(np.float32)
    idx = np.arange(2048)[None, :] + hop * np.arange(n)[:, None]
    frames = mono[idx] * win
    mag = np.log1p(np.abs(np.fft.rfft(frames, axis=1)))
    flux = np.maximum(0.0, np.diff(mag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")
    return np.maximum(flux, 0)


def analyze_audio(mono: np.ndarray, sr: int = ASR, hop: int = 512) -> dict:
    """BPM, 첫 박 위치, 클라이맥스(에너지 상승이 가장 큰 지점), 곡 길이."""
    env = onset_envelope(mono, sr, hop)
    fps = sr / hop
    dur = len(mono) / sr
    out = {"duration": round(dur, 2), "bpm": None, "beat_offset": 0.0, "peak": None, "peak_score": 0.0}
    if len(env) < fps * 8:
        return out
    # 템포: 자기상관에서 70~180 BPM 범위의 최대값 (60~90 은 두 배 후보도 비교)
    e = env - env.mean()
    ac = np.correlate(e, e, mode="full")[len(e) - 1:]
    lags = np.arange(len(ac))
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 70)
    if hi >= len(ac):
        return out
    weight = np.exp(-0.5 * (np.log2(60 * fps / np.maximum(lags[lo:hi], 1) / 120) / 0.9) ** 2)
    best = lo + int(np.argmax(ac[lo:hi] * weight))
    # 포물선 보간으로 소수 주기
    if 1 <= best < len(ac) - 1:
        y0, y1, y2 = ac[best - 1], ac[best], ac[best + 1]
        den = y0 - 2 * y1 + y2
        frac = 0.5 * (y0 - y2) / den if den != 0 else 0.0
    else:
        frac = 0.0
    lag = best + max(-0.5, min(0.5, frac))
    period = lag / fps
    out["bpm"] = round(float(60 / period), 1)
    # 박자 위상: 소수 주기 펄스열과의 상관이 가장 큰 시작점 (프레임 시간 = 창 중심)
    nb = int((len(env) / fps) / period)
    best_ph, best_v = 0.0, -1.0
    for ph in np.arange(0, period, 1 / fps):
        idx = np.round((ph + period * np.arange(nb)) * fps).astype(int)
        idx = idx[idx < len(env)]
        v = env[idx].sum()
        if v > best_v:
            best_v, best_ph = v, ph
    out["beat_offset"] = round(float((best_ph + ONSET_DELAY) % period), 3)
    # 클라이맥스: 2초 뒤 평균 RMS 가 직전 4초보다 가장 크게 오르는 지점 (곡 앞뒤 8초 제외)
    hop_s = int(sr * 0.1)
    m = len(mono) // hop_s
    rms = np.sqrt(np.mean(mono[: m * hop_s].reshape(m, hop_s) ** 2, axis=1) + 1e-9)
    after = np.convolve(rms, np.ones(20) / 20, mode="full")[19:19 + m]
    before = np.convolve(rms, np.ones(40) / 40, mode="full")[:m]
    score = after - before
    i0, i1 = 80, max(81, m - 80)
    if i1 > i0 + 1:
        # 클라이맥스 후보: 서로 10초 이상 떨어진 상승 지점 상위 5개 (가장 가까운 박으로 맞춤)
        sc = score.copy()
        cands = []
        for _ in range(5):
            i = i0 + int(np.argmax(sc[i0:i1]))
            if sc[i] <= 0 and cands:
                break
            t = i * 0.1
            t = out["beat_offset"] + round((t - out["beat_offset"]) / period) * period
            cands.append({"t": round(float(t), 3), "score": round(float(score[i] / (rms.mean() + 1e-9)), 3)})
            sc[max(0, i - 100): i + 100] = -np.inf
        best = max(cands, key=lambda c: c["score"])
        out["peak"], out["peak_score"] = best["t"], best["score"]
        out["peaks"] = sorted(cands, key=lambda c: c["t"])
    return out


def get_analysis(track: Track, cfg: Config) -> dict:
    if track.analysis.get("peaks") is not None or (track.analysis.get("duration") and not track.analysis.get("bpm")):
        return track.analysis
    a = analyze_audio(decode(track.path, cfg, sr=ASR, mono=True))
    track.analysis = a
    if track.meta_path:
        try:
            m = json.loads(track.meta_path.read_text(encoding="utf-8"))
            m["analysis"] = a
            track.meta_path.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    return a


def choose_track(cfg: Config, db: DB, style: str, preset_grade: str, rng: random.Random,
                 min_sec: float = 40, expected_reveal: float | None = None) -> Track | None:
    """분위기 일치 + 최근 미사용 + 뚜렷한 클라이맥스 + 충분한 길이를 점수로 고른다."""
    tracks, _ = scan_library(cfg)
    moods = set(MOOD_BY_STYLE.get(style, []))
    if preset_grade == "night":
        moods |= {"mysterious", "dark"}
    if not tracks and cfg.get("audio.auto_fetch", True):
        from .fetcher import AudioFetcher
        AudioFetcher(cfg, db).ensure_library(sorted(moods))
        tracks, _ = scan_library(cfg)
    elif cfg.get("audio.auto_fetch", True) and not any(moods & set(t.moods) for t in tracks):
        from .fetcher import AudioFetcher
        try:
            AudioFetcher(cfg, db).ensure_library(sorted(moods))
            tracks, _ = scan_library(cfg)
        except Exception as e:
            db.log("WARN", "audio", f"자동 수집 실패: {e}")
    if not tracks:
        return None
    used = [r["value"] for r in db.query("SELECT json_extract(meta_json,'$.music') AS value FROM videos "
                                         "WHERE variant LIKE 'edited%' ORDER BY id DESC LIMIT 12") if r["value"]]
    def score(t: Track) -> float:
        s = len(moods & set(t.moods)) * 2.0
        s -= 2.0 if str(t.path) in used else 0.0
        a = t.analysis
        if a.get("duration") and a["duration"] < min_sec:
            s -= 3.0
        if expected_reveal is not None and a.get("peaks"):
            s += min(1.5, peak_fit(t, expected_reveal) * 0.8)
        else:
            s += min(1.5, float(a.get("peak_score") or 0) * 0.5)
        return s + rng.random() * 0.5
    ranked = sorted(tracks, key=score, reverse=True)[:5]
    for t in ranked:
        try:
            get_analysis(t, cfg)
        except Exception:
            t.analysis = {"duration": 0}
    ranked.sort(key=score, reverse=True)
    best = ranked[0]
    return best if (best.analysis.get("duration") or 0) >= 10 else None


def generate_music(cfg: Config, prompt: str, seconds: float, out_dir: Path) -> Track | None:
    """AI 음악 생성 연동 (선택). music_api.endpoint 에 POST {prompt, duration} 를 보내
    오디오 바이트(audio/*) 또는 {"audio_url": ...} JSON 을 받는 범용 연동이다.
    사용하는 서비스의 상업적 이용 조건을 확인한 뒤 music_api.license 에 적는다."""
    import os
    ep = cfg.get("music_api.endpoint")
    if not ep:
        return None
    key = os.environ.get(cfg.get("music_api.api_key_env"), "")
    req = urllib.request.Request(ep, data=json.dumps({"prompt": prompt, "duration": seconds}).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        ctype = resp.headers.get("Content-Type", "")
        body = resp.read()
    if ctype.startswith("application/json"):
        url = json.loads(body).get("audio_url")
        if not url:
            raise RuntimeError("음악 API 응답에 audio_url 이 없습니다")
        with urllib.request.urlopen(url, timeout=600) as r2:
            body = r2.read()
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"ai_music_{abs(hash(prompt)) % 10**8}.mp3"
    p.write_bytes(body)
    lic = cfg.get("music_api.license", "")
    meta = {"title": prompt[:60], "artist": "AI", "license": lic, "source_url": ep, "moods": []}
    Path(str(p) + ".json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if lic.lower() not in [a.lower() for a in cfg.get("audio.allowed_licenses", DEFAULT_ALLOWED)]:
        return None
    return Track(p, meta["title"], "AI", lic, ep, [], meta_path=Path(str(p) + ".json"))


def music_offset(track: Track | None, reveal_t: float) -> float:
    """곡의 클라이맥스가 영상의 완성 공개 순간에 오도록 하는 곡 시작 위치(초).
    공개 시각보다 뒤에 있는 클라이맥스 후보 중 점수가 높은 것을 고르고, 없으면 박자만 맞춘다."""
    if not track:
        return 0.0
    a = track.analysis or {}
    cands = [c for c in a.get("peaks") or ([{"t": a["peak"], "score": a.get("peak_score", 0)}] if a.get("peak") else [])
             if c["t"] >= reveal_t]
    if cands:
        best = max(cands, key=lambda c: c["score"] - 0.01 * (c["t"] - reveal_t))
        return max(0.0, best["t"] - reveal_t)
    bpm = a.get("bpm") or 120
    period = 60 / bpm
    return ((a.get("beat_offset") or 0.0) - reveal_t) % period


def peak_fit(track: Track, reveal_t: float) -> float:
    """공개 시각 이후에 쓸 수 있는 클라이맥스가 있으면 그 점수, 없으면 0."""
    a = track.analysis or {}
    return max([c["score"] for c in a.get("peaks") or [] if c["t"] >= reveal_t] or [0.0])


def build_soundtrack(cfg: Config, total: float, track: Track | None, reveal_t: float,
                     build_span: tuple[float, float], transitions: list[float], out: Path,
                     rng: random.Random) -> dict:
    """음악 + 효과음 믹스 WAV 를 만든다. 반환: 사용 정보(출처 표기 포함)."""
    n = int(total * SR)
    mix = np.zeros((n, 2), dtype=np.float32)
    info: dict = {"music": None, "music_offset": 0.0, "sfx": [], "credits": []}
    beat_times: list[float] = []
    if track:
        music = decode(track.path, cfg)
        a = track.analysis or {}
        offset = music_offset(track, reveal_t)
        start = int(offset * SR)
        seg = music[start: start + n]
        if len(seg) < n:
            reps = int(np.ceil((start + n) / max(1, len(music)))) + 1
            seg = np.concatenate([music] * reps)[start: start + n]
        seg = fade(seg, 0.6, 2.0) * float(cfg.get("video.music_volume"))
        # 공개 순간 직전 0.4초 살짝 줄였다가 터뜨리는 강조 (덕킹)
        i0, i1 = int((reveal_t - 0.4) * SR), int(reveal_t * SR)
        if 0 < i0 < i1 < len(seg):
            seg[i0:i1] *= np.linspace(1.0, 0.55, i1 - i0)[:, None]
        mix[: len(seg)] += seg
        if a.get("bpm"):
            period = 60 / a["bpm"]
            first = (a.get("beat_offset", 0) - offset) % period
            beat_times = list(np.arange(first, total, period))
        info.update(music=str(track.path), music_offset=round(offset, 2), bpm=a.get("bpm"),
                    music_peak=a.get("peak"), credit=track.credit())
        info["credits"].append(track.credit())
    vol = float(cfg.get("video.sfx_volume"))
    used_sfx: list[Path] = []

    def put(clip: np.ndarray, t: float, gain: float) -> None:
        i = max(0, int(t * SR)); j = min(n, i + len(clip))
        if j > i:
            mix[i:j] += clip[: j - i] * gain

    places = sfx_files(cfg, "place")[:12]
    if places:
        clips = [decode(p, cfg)[: int(0.6 * SR)] for p in places]
        used_sfx += places[:len(clips)]
        if beat_times:
            # 8분음표 격자에 맞춘 블록 소리 (일부 건너뛰어 자연스럽게)
            half = (beat_times[1] - beat_times[0]) / 2 if len(beat_times) > 1 else 0.25
            t = next((b for b in beat_times if b >= build_span[0]), build_span[0])
            while t < build_span[1]:
                if rng.random() < 0.75:
                    put(clips[rng.randrange(len(clips))], t, 0.22 * vol)
                t += half
        else:
            t = build_span[0]
            while t < build_span[1]:
                put(clips[rng.randrange(len(clips))], t, 0.22 * vol)
                t += rng.uniform(0.12, 0.35)
        info["sfx"].append("place")
    wh = sfx_files(cfg, "whoosh")
    if wh:
        clips = [decode(p, cfg) for p in wh[:4]]
        used_sfx += wh[:4]
        for k, tt in enumerate(transitions):
            put(clips[k % len(clips)], tt - 0.25, 0.55 * vol)
        info["sfx"].append("whoosh")
    rs = sfx_files(cfg, "riser")
    if rs:
        r = decode(rs[0], cfg)
        r = r[-int(min(len(r) / SR, 3.0) * SR):]
        put(fade(r, 0.3, 0.05), reveal_t - len(r) / SR, 0.5 * vol)
        used_sfx.append(rs[0])
        info["sfx"].append("riser")
    im = sfx_files(cfg, "impact")
    if im:
        put(decode(im[0], cfg), reveal_t, 0.7 * vol)
        used_sfx.append(im[0])
        info["sfx"].append("impact")
    comp = sfx_files(cfg, "complete")
    if comp:
        put(decode(comp[0], cfg), reveal_t + 0.15, 0.8 * vol)
        used_sfx.append(comp[0])
        info["sfx"].append("complete")
    for p in dict.fromkeys(used_sfx):
        c = sfx_credit(p)
        if c:
            info["credits"].append(f"SFX: {c}")
    peak = float(np.max(np.abs(mix))) if n else 0.0
    if peak > 0.98:
        mix *= 0.98 / peak
    write_wav(out, mix)
    info["has_audio"] = bool(track or info["sfx"])
    info["beats"] = [round(b, 3) for b in beat_times[:400]]
    return info
