"""인기 건축 주제 분석: 최근 30일 조회수가 높은 마인크래프트 건축 영상의 제목을 모아
건축물 종류/양식/지형 키워드별 가중치를 만들고, 다음 기획에 반영한다.

YouTube Data API search.list (호출당 100 할당량 단위) + videos.list 를 쓴다.
인증: 환경 변수 YOUTUBE_API_KEY (권장) 또는 연결된 OAuth 계정.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from mcbuildai.config import Config
from mcbuildai.db import DB

SYNONYMS = {
    "btype": {
        "castle": ["castle", "fortress", "keep", "stronghold"], "cathedral": ["cathedral", "church", "chapel"],
        "tower": ["tower", "wizard tower", "watchtower"], "skyscraper": ["skyscraper", "city", "highrise"],
        "temple": ["temple", "shrine"], "pagoda": ["pagoda"], "lighthouse": ["lighthouse"],
        "mansion": ["mansion", "villa", "palace", "modern house"], "house": ["house", "cottage", "starter", "home", "cabin"],
        "ziggurat": ["pyramid", "ziggurat"], "citadel": ["citadel", "kingdom", "mega base", "megabase"],
        "hanok": ["korean", "hanok"],
    },
    "style": {
        "medieval": ["medieval"], "gothic": ["gothic", "dark"], "fantasy": ["fantasy", "elven", "elf", "magic"],
        "japanese": ["japanese", "japan", "sakura", "cherry"], "chinese": ["chinese", "china"],
        "modern": ["modern", "minimalist"], "futuristic": ["futuristic", "sci-fi", "scifi", "cyberpunk", "future"],
        "steampunk": ["steampunk"], "nordic": ["viking", "nordic", "norse"], "desert": ["desert", "egyptian", "egypt"],
        "classical": ["greek", "roman", "classical"], "korean": ["korean"],
    },
    "terrain": {
        "island": ["island"], "mountain": ["mountain", "cliff"], "sea": ["ocean", "sea", "underwater"],
        "desert": ["desert"], "snow": ["snow", "winter", "ice"], "forest": ["forest", "jungle"],
        "lava": ["lava", "nether", "volcano"],
    },
}
STOP = set("""a an the and or of in to for with my i you we is it this that on at by from how build building built
minecraft mc shorts short timelapse time lapse tutorial easy best ever made vs part ep episode 1 2 3 4 5 new""".split())


def _service(cfg: Config):
    from googleapiclient.discovery import build
    key = os.environ.get(cfg.get("youtube.api_key_env", "YOUTUBE_API_KEY"), "")
    if key:
        return build("youtube", "v3", developerKey=key, cache_discovery=False)
    from .client import _service as oauth_service
    return oauth_service(cfg)


def fetch_trending(cfg: Config, days: int = 30, per_query: int = 50) -> list[dict]:
    yt = _service(cfg)
    after = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds").replace("+00:00", "Z")
    vids: dict[str, dict] = {}
    for q in cfg.get("youtube.trend_queries", ["minecraft build timelapse"]):
        r = yt.search().list(part="snippet", q=q, type="video", order="viewCount", publishedAfter=after,
                             maxResults=min(50, per_query), relevanceLanguage="en", videoDuration="short").execute()
        for it in r.get("items", []):
            vid = it["id"].get("videoId")
            if vid:
                vids[vid] = {"youtube_id": vid, "title": it["snippet"]["title"],
                             "channel": it["snippet"]["channelTitle"], "published": it["snippet"]["publishedAt"]}
    ids = list(vids)
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            vids[it["id"]]["views"] = int(it.get("statistics", {}).get("viewCount", 0))
    out = [v for v in vids.values() if "views" in v]
    return sorted(out, key=lambda v: -v["views"])


def analyze_titles(videos: list[dict]) -> tuple[dict[str, dict[str, float]], list[str]]:
    """(카테고리 가중치, 상위 키워드). 가중치는 조회수 로그 합을 평균 1 로 맞추고 0.7~1.6 으로 제한."""
    scores: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    words: Counter = Counter()
    for v in videos:
        t = v["title"].lower()
        w = math.log10(max(10, v.get("views", 0)))
        for cat, table in SYNONYMS.items():
            for value, syns in table.items():
                if any(re.search(rf"\b{re.escape(s)}\b", t) for s in syns):
                    scores[cat][value] += w
        for tok in re.findall(r"[a-z][a-z\-]{2,}", t):
            if tok not in STOP:
                words[tok] += w
    weights: dict[str, dict[str, float]] = {}
    for cat, table in SYNONYMS.items():
        vals = {k: scores[cat].get(k, 0.0) for k in table}
        mean = sum(vals.values()) / max(1, len(vals))
        if mean <= 0:
            continue
        weights[cat] = {k: round(max(0.7, min(1.6, 0.7 + 0.3 * v / mean)), 3) for k, v in vals.items()}
    return weights, [w for w, _ in words.most_common(30)]


def update_trends(cfg: Config, db: DB) -> dict:
    videos = fetch_trending(cfg)
    weights, keywords = analyze_titles(videos)
    db.kv_set("trend_weights", weights)
    db.kv_set("trend_keywords", keywords)
    db.kv_set("trend_top_videos", videos[:30])
    db.kv_set("trend_updated_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    db.log("INFO", "trends", f"인기 영상 {len(videos)}개 분석, 키워드: {', '.join(keywords[:10])}")
    return {"videos": len(videos), "keywords": keywords[:15], "weights": weights}
