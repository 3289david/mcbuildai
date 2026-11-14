"""음악/효과음 자동 수집.

출처
- Openverse (https://api.openverse.org) : 키 없이 사용. Jamendo, Freesound, Wikimedia 의 CC 음원을 라이선스로 걸러 검색.
- Jamendo (https://api.jamendo.com/v3.0) : JAMENDO_CLIENT_ID 가 있으면 사용. 다운로드 허용(audiodownload_allowed)된 곡만.
- Freesound (https://freesound.org/apiv2) : FREESOUND_API_KEY 가 있으면 효과음 검색 (CC0 미리듣기 파일).

라이선스 규칙
- 받는 것: CC0, Public Domain Mark, CC BY (출처 표기 자동).
- 받지 않는 것: NC(비상업), ND(변경 금지), SA(동일조건) 계열. 수익 창출 채널과 편집(자르기/믹싱)에 맞지 않기 때문이다.
- 받은 파일마다 같은 이름의 .json 에 제목, 작가, 라이선스, 원본 주소, 출처 표기 문구를 저장한다.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mcbuildai.config import Config
from mcbuildai.db import DB
from mcbuildai.editor.ff import bins

UA = "mcbuildai/1.1 (Minecraft build timelapse automation)"
MAX_BYTES = 40 * 1024 * 1024

MOOD_QUERIES = {
    "epic": ["epic orchestral", "cinematic epic", "heroic orchestral"],
    "adventure": ["adventure orchestral", "adventure instrumental"],
    "calm": ["calm piano", "peaceful ambient", "relaxing instrumental"],
    "asian": ["japanese koto", "asian traditional instrumental", "chinese instrumental"],
    "upbeat": ["upbeat electronic", "upbeat instrumental"],
    "chill": ["lofi chill", "chill instrumental"],
    "electronic": ["electronic synth", "synthwave"],
    "mysterious": ["mysterious ambient", "fantasy ambient"],
    "dark": ["dark ambient", "dark cinematic"],
    "warm": ["acoustic guitar", "warm acoustic"],
    "elegant": ["classical strings", "elegant piano"],
}

SFX_QUERIES = {
    "place": ["wood block place", "stone tap", "click wood"],
    "whoosh": ["whoosh", "swoosh transition"],
    "complete": ["success chime", "magic reveal", "bell chime"],
    "riser": ["riser", "cinematic riser"],
    "impact": ["cinematic impact", "boom impact"],
}
SFX_MAX_SEC = {"place": 1.2, "whoosh": 2.5, "complete": 4.0, "riser": 8.0, "impact": 4.0}


@dataclass
class Candidate:
    provider: str
    provider_id: str
    title: str
    artist: str
    license: str          # CC0 / PDM / CC-BY
    license_url: str
    source_url: str
    download_url: str
    duration: float
    attribution: str
    tags: list[str]


def _get_json(url: str, headers: dict | None = None, timeout: float = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def normalize_license(code: str, url: str = "") -> str | None:
    """허용 라이선스만 표준 이름으로, 나머지는 None."""
    c = (code or "").lower().strip()
    u = (url or "").lower()
    if c in ("cc0", "creative commons 0") or "publicdomain/zero" in u:
        return "CC0"
    if c == "pdm" or "publicdomain/mark" in u:
        return "PDM"
    if c in ("by", "cc-by", "attribution") or re.search(r"/licenses/by/\d", u):
        return "CC-BY"
    return None


# ---------------------------------------------------------------------------
# 검색
# ---------------------------------------------------------------------------
def search_openverse(query: str, category: str, max_sec: float, min_sec: float = 0) -> list[Candidate]:
    params = {"q": query, "license": "cc0,pdm,by", "category": category, "page_size": 20,
              "mature": "false", "filter_dead": "true"}
    url = "https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    out = []
    for r in data.get("results", []):
        lic = normalize_license(r.get("license", ""), r.get("license_url", ""))
        dur = (r.get("duration") or 0) / 1000.0
        if not lic or not r.get("url") or (dur and (dur > max_sec or dur < min_sec)):
            continue
        out.append(Candidate("openverse", str(r["id"]), r.get("title") or "untitled", r.get("creator") or "unknown",
                             lic, r.get("license_url") or "", r.get("foreign_landing_url") or "", r["url"], dur,
                             r.get("attribution") or "", [t.get("name", "") for t in r.get("tags") or []]))
    return out


def search_jamendo(client_id: str, query: str, max_sec: float, min_sec: float) -> list[Candidate]:
    params = {"client_id": client_id, "format": "json", "limit": 30,
              "vocalinstrumental": "instrumental", "audiodlformat": "mp32", "include": "musicinfo",
              "order": "popularity_total"}
    tags = "+".join(urllib.parse.quote(w) for w in query.split())
    data = _get_json("https://api.jamendo.com/v3.0/tracks/?" + urllib.parse.urlencode(params) + "&fuzzytags=" + tags)
    out = []
    for r in data.get("results", []):
        lic = normalize_license("", r.get("license_ccurl", ""))
        dur = float(r.get("duration") or 0)
        if not lic or not r.get("audiodownload_allowed") or not r.get("audiodownload"):
            continue
        if dur > max_sec or dur < min_sec:
            continue
        out.append(Candidate("jamendo", str(r["id"]), r.get("name", ""), r.get("artist_name", ""), lic,
                             r.get("license_ccurl", ""), r.get("shareurl", ""), r["audiodownload"], dur,
                             f"\"{r.get('name', '')}\" by {r.get('artist_name', '')} ({r.get('license_ccurl', '')})",
                             (r.get("musicinfo") or {}).get("tags", {}).get("genres", [])))
    return out


def search_freesound(token: str, query: str, max_sec: float) -> list[Candidate]:
    params = {"query": query, "filter": f'license:"Creative Commons 0" duration:[0.05 TO {max_sec}]',
              "fields": "id,name,username,license,previews,duration,url,tags", "page_size": 20,
              "sort": "rating_desc", "token": token}
    data = _get_json("https://freesound.org/apiv2/search/text/?" + urllib.parse.urlencode(params))
    out = []
    for r in data.get("results", []):
        prev = (r.get("previews") or {}).get("preview-hq-mp3")
        lic = normalize_license("", r.get("license", ""))
        if not prev or not lic:
            continue
        out.append(Candidate("freesound", str(r["id"]), r.get("name", ""), r.get("username", ""), lic,
                             r.get("license", ""), r.get("url", ""), prev, float(r.get("duration") or 0),
                             f"\"{r.get('name', '')}\" by {r.get('username', '')} (CC0)", r.get("tags") or []))
    return out


# ---------------------------------------------------------------------------
# 다운로드와 저장
# ---------------------------------------------------------------------------
def _download(url: str, dest: Path) -> Path:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        ctype = r.headers.get("Content-Type", "")
        if "text/html" in ctype:
            raise RuntimeError("오디오가 아닌 페이지가 반환됨")
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise RuntimeError("파일이 너무 큼")
    ext = {"audio/mpeg": ".mp3", "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
           "audio/flac": ".flac", "application/ogg": ".ogg"}.get(ctype.split(";")[0].strip(), "")
    if not ext:
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        if ext not in (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".oga"):
            ext = ".mp3"
    out = dest.with_suffix(ext)
    out.write_bytes(data)
    return out


def _probe_seconds(path: Path, cfg: Config) -> float:
    _, fp = bins(cfg)
    p = subprocess.run([fp, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
                       capture_output=True, text=True)
    try:
        return float(p.stdout.strip())
    except ValueError:
        return 0.0


def _known_ids(folder: Path) -> set[str]:
    ids = set()
    for j in folder.rglob("*.json"):
        try:
            m = json.loads(j.read_text(encoding="utf-8"))
            if m.get("provider"):
                ids.add(f"{m['provider']}:{m.get('provider_id')}")
        except Exception:
            pass
    return ids


def _save(cfg: Config, c: Candidate, folder: Path, stem: str, extra: dict) -> Path | None:
    folder.mkdir(parents=True, exist_ok=True)
    try:
        path = _download(c.download_url, folder / stem)
    except Exception:
        return None
    secs = _probe_seconds(path, cfg)
    if secs <= 0.05:
        path.unlink(missing_ok=True)
        return None
    meta = {"title": c.title, "artist": c.artist, "license": c.license, "license_url": c.license_url,
            "source_url": c.source_url, "attribution": c.attribution, "provider": c.provider,
            "provider_id": c.provider_id, "duration": round(secs, 2), "tags": c.tags[:20],
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **extra}
    Path(str(path) + ".json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


class AudioFetcher:
    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db
        self.sources = cfg.get("audio.sources", ["openverse"])
        self.jamendo_id = os.environ.get(cfg.get("audio.jamendo_client_id_env", "JAMENDO_CLIENT_ID"), "")
        self.fs_token = os.environ.get(cfg.get("audio.freesound_token_env", "FREESOUND_API_KEY"), "")

    def _music_candidates(self, mood: str) -> list[Candidate]:
        lo, hi = float(self.cfg.get("audio.music_min_sec", 60)), float(self.cfg.get("audio.music_max_sec", 420))
        out: list[Candidate] = []
        for q in MOOD_QUERIES.get(mood, [mood]):
            if "jamendo" in self.sources and self.jamendo_id:
                try:
                    out += search_jamendo(self.jamendo_id, q, hi, lo)
                except Exception as e:
                    self.db.log("WARN", "audio", f"Jamendo 검색 실패 ({q}): {e}")
            if "openverse" in self.sources:
                try:
                    out += search_openverse(q + " instrumental" if "instrumental" not in q else q, "music", hi, lo)
                except Exception as e:
                    self.db.log("WARN", "audio", f"Openverse 검색 실패 ({q}): {e}")
            time.sleep(1.0)
        return out

    def fetch_music(self, mood: str, count: int) -> list[Path]:
        folder = self.cfg.path_of("paths.music_dir") / "auto"
        known = _known_ids(self.cfg.path_of("paths.music_dir"))
        got: list[Path] = []
        for c in self._music_candidates(mood):
            if len(got) >= count:
                break
            key = f"{c.provider}:{c.provider_id}"
            if key in known:
                continue
            known.add(key)
            stem = re.sub(r"[^\w\-]+", "_", f"{mood}_{c.provider}_{c.provider_id}")[:80]
            p = _save(self.cfg, c, folder, stem, {"moods": [mood]})
            if p:
                got.append(p)
                self.db.log("INFO", "audio", f"음악 수집: {c.title} - {c.artist} [{c.license}] ({c.provider})")
        return got

    def fetch_sfx(self, kind: str, count: int) -> list[Path]:
        folder = self.cfg.path_of("paths.sfx_dir") / "auto"
        known = _known_ids(self.cfg.path_of("paths.sfx_dir"))
        max_sec = SFX_MAX_SEC.get(kind, 4.0)
        cands: list[Candidate] = []
        for q in SFX_QUERIES.get(kind, [kind]):
            if "freesound" in self.sources and self.fs_token:
                try:
                    cands += search_freesound(self.fs_token, q, max_sec)
                except Exception as e:
                    self.db.log("WARN", "audio", f"Freesound 검색 실패 ({q}): {e}")
            if "openverse" in self.sources:
                try:
                    cands += [c for c in search_openverse(q, "sound_effect", max_sec) if c.license in ("CC0", "PDM")]
                except Exception as e:
                    self.db.log("WARN", "audio", f"Openverse 효과음 검색 실패 ({q}): {e}")
            time.sleep(1.0)
        got: list[Path] = []
        for c in cands:
            if len(got) >= count:
                break
            key = f"{c.provider}:{c.provider_id}"
            if key in known:
                continue
            known.add(key)
            stem = re.sub(r"[^\w\-]+", "_", f"{kind}_{c.provider}_{c.provider_id}")[:80]
            p = _save(self.cfg, c, folder, stem, {"kind": kind})
            if p:
                got.append(p)
        if got:
            self.db.log("INFO", "audio", f"효과음 수집: {kind} {len(got)}개")
        return got

    def ensure_library(self, moods: list[str] | None = None) -> dict:
        """분위기별 곡 수와 효과음 종류별 수가 기준보다 적으면 채운다."""
        from .library import scan_library, sfx_files
        need_music = int(self.cfg.get("audio.min_tracks_per_mood", 3))
        need_sfx = int(self.cfg.get("audio.min_sfx_per_kind", 3))
        tracks, _ = scan_library(self.cfg)
        report = {"music": {}, "sfx": {}}
        for mood in moods or list(MOOD_QUERIES):
            have = sum(1 for t in tracks if mood in t.moods)
            if have < need_music:
                report["music"][mood] = len(self.fetch_music(mood, need_music - have))
        for kind in SFX_QUERIES:
            have = len(sfx_files(self.cfg, kind))
            if have < need_sfx:
                report["sfx"][kind] = len(self.fetch_sfx(kind, need_sfx - have))
        self.db.kv_set("audio_fetch_report", {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                              **report})
        return report
