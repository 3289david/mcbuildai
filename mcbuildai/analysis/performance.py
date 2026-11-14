"""AI 성과 분석 및 개선.

수집된 조회수/평균 시청률을 건축물 종류, 양식, 연출, 카메라, 영상 길이, 실험 버전별로 묶어 비교하고,
기획 단계에서 쓰는 가중치(category_weights)로 되먹임한다. 데이터가 적을 때는 1.0 쪽으로 수축시켜
몇 개의 영상 결과에 과하게 끌려가지 않게 한다.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

from mcbuildai.db import DB

CATEGORIES = ["btype", "style", "scale", "palette", "terrain", "roof", "timelapse_style", "camera_style",
              "time_of_day", "length_bucket", "variant"]
PRIOR = 3.0  # 수축 강도 (가상의 평균 영상 수)


def _length_bucket(sec: float | None) -> str:
    if not sec:
        return "unknown"
    return "<30s" if sec < 30 else "30-45s" if sec < 45 else "45-60s"


def video_rows(db: DB) -> list[dict]:
    """업로드된 편집 영상별 최신 성과 + 속성."""
    rows = db.query("""
        SELECT u.youtube_id, v.duration, v.meta_json, b.dna_json, b.timelapse_style, b.camera_style,
               a.views, a.avg_view_percentage, a.avg_view_duration, a.likes, a.fetched_at
        FROM uploads u
        JOIN videos v ON v.id = u.video_id
        JOIN buildings b ON b.id = v.building_id
        JOIN analytics a ON a.id = (SELECT id FROM analytics WHERE youtube_id=u.youtube_id ORDER BY id DESC LIMIT 1)
        WHERE u.youtube_id IS NOT NULL""")
    out = []
    for r in rows:
        dna = json.loads(r["dna_json"])
        meta = json.loads(r["meta_json"] or "{}")
        out.append({
            "youtube_id": r["youtube_id"], "views": r["views"] or 0, "avp": r["avg_view_percentage"],
            "avd": r["avg_view_duration"], "likes": r["likes"] or 0, "duration": r["duration"],
            "btype": dna.get("btype"), "style": dna.get("style"), "scale": dna.get("scale"),
            "palette": dna.get("palette"), "terrain": dna.get("terrain"), "roof": dna.get("roof"),
            "time_of_day": dna.get("time_of_day"),
            "timelapse_style": r["timelapse_style"], "camera_style": r["camera_style"],
            "length_bucket": _length_bucket(r["duration"]), "variant": meta.get("variant", "A"),
        })
    return out


def score(row: dict, mean_log_views: float) -> float:
    """조회수(로그, 채널 평균 대비)와 평균 시청률을 합친 점수. 1.0 이 평균."""
    v = math.log1p(row["views"]) / max(0.1, mean_log_views)
    if row["avp"] is not None:
        return 0.5 * v + 0.5 * (row["avp"] / 70.0)
    return v


def summarize(db: DB) -> dict:
    rows = video_rows(db)
    if not rows:
        return {"videos": 0, "by_category": {}}
    mlv = sum(math.log1p(r["views"]) for r in rows) / len(rows)
    by: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        s = score(r, mlv)
        for c in CATEGORIES:
            by[c][str(r.get(c))].append((s, r))
    out: dict = {"videos": len(rows), "total_views": sum(r["views"] for r in rows), "by_category": {}}
    for c, groups in by.items():
        stats = []
        for val, items in groups.items():
            n = len(items)
            mean_s = sum(s for s, _ in items) / n
            shrunk = (mean_s * n + 1.0 * PRIOR) / (n + PRIOR)
            avps = [r["avp"] for _, r in items if r["avp"] is not None]
            stats.append({"value": val, "count": n, "avg_views": round(sum(r["views"] for _, r in items) / n, 1),
                          "avg_view_percentage": round(sum(avps) / len(avps), 1) if avps else None,
                          "score": round(mean_s, 3), "weight": round(shrunk, 3)})
        out["by_category"][c] = sorted(stats, key=lambda x: -x["weight"])
    return out


def category_weights(db: DB) -> dict[str, dict[str, float]]:
    """기획 가중치 (0.5~2.0). 성과 데이터가 없으면 빈 dict (모두 1.0 으로 취급)."""
    s = summarize(db)
    w: dict[str, dict[str, float]] = {}
    for c, stats in s.get("by_category", {}).items():
        w[c] = {x["value"]: max(0.5, min(2.0, x["weight"])) for x in stats}
    return w


def popular(db: DB, category: str, top: int = 5) -> list[dict]:
    return summarize(db).get("by_category", {}).get(category, [])[:top]


def experiment_report(db: DB) -> list[dict]:
    """같은 건축물의 A/B 편집 버전 성과 비교."""
    rows = db.query("""
        SELECT v.building_id, json_extract(v.meta_json,'$.variant') AS variant, u.youtube_id,
               (SELECT views FROM analytics a WHERE a.youtube_id=u.youtube_id ORDER BY id DESC LIMIT 1) AS views,
               (SELECT avg_view_percentage FROM analytics a WHERE a.youtube_id=u.youtube_id ORDER BY id DESC LIMIT 1) AS avp
        FROM videos v JOIN uploads u ON u.video_id=v.id
        WHERE v.variant LIKE 'edited%' AND u.youtube_id IS NOT NULL""")
    by_b: dict[int, dict] = defaultdict(dict)
    for r in rows:
        by_b[r["building_id"]][r["variant"] or "A"] = r
    out = []
    for bid, d in by_b.items():
        if "A" in d and "B" in d:
            a, b = d["A"], d["B"]
            out.append({"building_id": bid, "A_views": a["views"], "B_views": b["views"],
                        "A_avp": a["avp"], "B_avp": b["avp"],
                        "winner": "A" if (a["views"] or 0) >= (b["views"] or 0) else "B"})
    return out
