"""YouTube 연동: OAuth 계정 연결, 업로드(예약/공개 범위), 썸네일, 상태 확인, 재시도, 게시 영상 관리, 통계 수집.

준비: Google Cloud 콘솔에서 YouTube Data API v3 와 YouTube Analytics API 를 켜고
'데스크톱 앱' OAuth 클라이언트 JSON 을 내려받아 youtube.client_secrets 경로에 둔다.
"""
from __future__ import annotations

import json
import random
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from mcbuildai.config import Config
from mcbuildai.db import DB, now_iso

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
RETRIABLE_STATUS = {500, 502, 503, 504}


class YouTubeError(RuntimeError):
    pass


def _creds(cfg: Config, interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    token = cfg.path_of("youtube.token_file")
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        if not interactive:
            raise YouTubeError("YouTube 계정이 연결되지 않았습니다. 'python -m mcbuildai youtube-auth' 를 실행하세요.")
        from google_auth_oauthlib.flow import InstalledAppFlow
        secrets = cfg.path_of("youtube.client_secrets")
        if not secrets.exists():
            raise YouTubeError(f"OAuth 클라이언트 파일이 없습니다: {secrets}")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
        token.write_text(creds.to_json(), encoding="utf-8")
    return creds


def connect_account(cfg: Config) -> dict:
    """YouTube 계정 연결 (브라우저 OAuth)."""
    creds = _creds(cfg, interactive=True)
    yt = _service(cfg, creds)
    ch = yt.channels().list(part="snippet", mine=True).execute()
    items = ch.get("items", [])
    return {"channel_id": items[0]["id"], "title": items[0]["snippet"]["title"]} if items else {}


def _service(cfg: Config, creds=None):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds or _creds(cfg), cache_discovery=False)


def _analytics(cfg: Config):
    from googleapiclient.discovery import build
    return build("youtubeAnalytics", "v2", credentials=_creds(cfg), cache_discovery=False)


# 요일(평일/주말) x 영상 종류(쇼츠/롱폼)별 미국 동부 시간(EST/EDT) 추천 업로드 시각.
# 숏츠: 평일 점심시간대(오후 12~3시) 중 피크, 주말 오전(9~11시) 중 피크.
# 롱폼: 평일 오후(2~6시, 특히 3~4시가 가장 추천) 중 피크, 주말(오전 9시~오후 6시) 중 피크.
SCHEDULE_BY_TYPE = {
    "short": {"weekday": "13:00", "weekend": "10:00"},
    "long": {"weekday": "15:30", "weekend": "11:00"},
}


def next_publish_time(cfg: Config, db: DB, variant: str = "short") -> datetime:
    """예약 업로드 시각: 요일(평일/주말)과 영상 종류(쇼츠/롱폼)에 맞는 추천 시간대 중
    아직 비어있는 가장 가까운 미래 슬롯을 고른다."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(cfg.get("youtube.timezone"))
    now = datetime.now(tz)
    taken = {r["publish_at"] for r in db.query("SELECT publish_at FROM uploads WHERE publish_at IS NOT NULL")}
    times = SCHEDULE_BY_TYPE.get(variant, SCHEDULE_BY_TYPE["short"])
    for day in range(0, 60):
        d = (now + timedelta(days=day)).date()
        is_weekend = d.weekday() >= 5  # 5=토, 6=일
        hm = times["weekend"] if is_weekend else times["weekday"]
        h, m = (int(x) for x in hm.split(":"))
        slot = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
        iso = slot.isoformat()
        if slot > now + timedelta(minutes=20) and iso not in taken:
            return slot
    raise YouTubeError("60일 안에 빈 예약 슬롯이 없습니다")


class Uploader:
    def __init__(self, cfg: Config, db: DB):
        self.cfg = cfg
        self.db = db

    def upload(self, upload_id: int) -> dict:
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
        up = self.db.one("SELECT u.*, v.path AS video_path, v.building_id FROM uploads u JOIN videos v ON v.id=u.video_id "
                         "WHERE u.id=?", (upload_id,))
        if not up:
            raise YouTubeError(f"업로드 {upload_id} 없음")
        yt = _service(self.cfg)
        status = {"privacyStatus": up["privacy"], "selfDeclaredMadeForKids": False}
        if up["publish_at"]:
            status["privacyStatus"] = "private"
            status["publishAt"] = datetime.fromisoformat(up["publish_at"]).astimezone().isoformat()
        extra = json.loads(up.get("extra_json") or "{}")
        lang = extra.get("language") or self.cfg.get("youtube.default_language")
        body = {
            "snippet": {"title": up["title"][:100], "description": up["description"][:5000],
                        "tags": json.loads(up["tags"] or "[]")[:30],
                        "categoryId": str(self.cfg.get("youtube.category_id")),
                        "defaultLanguage": lang,
                        "defaultAudioLanguage": lang},
            "status": status,
        }
        parts = "snippet,status"
        if extra.get("localizations"):
            body["localizations"] = extra["localizations"]
            parts += ",localizations"
        media = MediaFileUpload(up["video_path"], chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
        req = yt.videos().insert(part=parts, body=body, media_body=media)
        response, retry = None, 0
        self.db.execute("UPDATE uploads SET status='uploading', attempts=attempts+1, updated_at=? WHERE id=?",
                        (now_iso(), upload_id))
        while response is None:
            try:
                _, response = req.next_chunk()
            except HttpError as e:
                if e.resp.status in RETRIABLE_STATUS and retry < 8:
                    retry += 1
                    time.sleep(min(60, 2 ** retry + random.random()))
                    continue
                raise
            except (ConnectionError, TimeoutError, OSError):
                if retry < 8:
                    retry += 1
                    time.sleep(min(60, 2 ** retry + random.random()))
                    continue
                raise
        vid = response["id"]
        url = (f"https://youtube.com/shorts/{vid}" if up["video_path"].endswith(("edited.mp4", "edited_b.mp4"))
               else f"https://youtube.com/watch?v={vid}")
        self.db.execute("UPDATE uploads SET youtube_id=?, url=?, status='uploaded', error=NULL, updated_at=? WHERE id=?",
                        (vid, url, now_iso(), upload_id))
        thumb = Path(extra.get("thumbnail") or (Path(up["video_path"]).parent / "thumb.jpg"))
        if thumb.exists():
            try:
                yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")).execute()
            except HttpError as e:
                # 맞춤 썸네일은 채널 인증(전화번호)이 필요하다
                self.db.log("WARN", "youtube", f"썸네일 설정 실패 ({vid}): {e}")
        self.db.log("INFO", "youtube", f"업로드 완료: {url}")
        return {"youtube_id": vid, "url": url}

    def check_status(self, youtube_id: str) -> dict:
        yt = _service(self.cfg)
        r = yt.videos().list(part="status,processingDetails,snippet", id=youtube_id).execute()
        items = r.get("items", [])
        if not items:
            return {"exists": False}
        it = items[0]
        return {"exists": True, "uploadStatus": it["status"].get("uploadStatus"),
                "privacyStatus": it["status"].get("privacyStatus"), "publishAt": it["status"].get("publishAt"),
                "processing": (it.get("processingDetails") or {}).get("processingStatus"),
                "rejectionReason": it["status"].get("rejectionReason"), "title": it["snippet"]["title"]}

    def refresh_statuses(self) -> int:
        rows = self.db.query("SELECT id, youtube_id FROM uploads WHERE youtube_id IS NOT NULL AND status IN "
                             "('uploaded','scheduled','processing')")
        n = 0
        for r in rows:
            st = self.check_status(r["youtube_id"])
            if not st["exists"]:
                self.db.execute("UPDATE uploads SET status='missing', updated_at=? WHERE id=?", (now_iso(), r["id"]))
            else:
                new = "rejected" if st.get("uploadStatus") == "rejected" else (
                    "scheduled" if st.get("publishAt") and st.get("privacyStatus") == "private" else
                    "published" if st.get("privacyStatus") == "public" else "uploaded")
                self.db.execute("UPDATE uploads SET status=?, privacy=?, updated_at=? WHERE id=?",
                                (new, st.get("privacyStatus"), now_iso(), r["id"]))
            n += 1
        return n

    def set_privacy(self, youtube_id: str, privacy: str) -> None:
        yt = _service(self.cfg)
        yt.videos().update(part="status", body={"id": youtube_id, "status": {"privacyStatus": privacy}}).execute()
        self.db.execute("UPDATE uploads SET privacy=?, updated_at=? WHERE youtube_id=?", (privacy, now_iso(), youtube_id))

    def list_channel_videos(self, max_results: int = 50) -> list[dict]:
        yt = _service(self.cfg)
        ch = yt.channels().list(part="contentDetails", mine=True).execute()
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        r = yt.playlistItems().list(part="snippet,contentDetails", playlistId=uploads, maxResults=max_results).execute()
        return [{"youtube_id": it["contentDetails"]["videoId"], "title": it["snippet"]["title"],
                 "published": it["contentDetails"].get("videoPublishedAt")} for it in r.get("items", [])]


def collect_analytics(cfg: Config, db: DB) -> int:
    """조회수, 좋아요, 댓글(Data API) + 평균 시청 시간/평균 시청률(Analytics API) 수집."""
    ids = [r["youtube_id"] for r in db.query("SELECT youtube_id FROM uploads WHERE youtube_id IS NOT NULL")]
    if not ids:
        return 0
    yt = _service(cfg)
    stats: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        r = yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for it in r.get("items", []):
            s = it.get("statistics", {})
            stats[it["id"]] = {"views": int(s.get("viewCount", 0)), "likes": int(s.get("likeCount", 0)),
                               "comments": int(s.get("commentCount", 0))}
    try:
        an = _analytics(cfg)
        start = (date.today() - timedelta(days=365)).isoformat()
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            rep = an.reports().query(ids="channel==MINE", startDate=start, endDate=date.today().isoformat(),
                                     metrics="averageViewDuration,averageViewPercentage", dimensions="video",
                                     filters="video==" + ",".join(chunk), maxResults=200).execute()
            for row in rep.get("rows", []) or []:
                vid, avd, avp = row[0], row[1], row[2]
                stats.setdefault(vid, {}).update(avg_view_duration=float(avd), avg_view_percentage=float(avp))
    except Exception as e:
        db.log("WARN", "analytics", f"Analytics API 조회 실패 (조회수만 저장): {e}")
    ts = now_iso()
    for vid, s in stats.items():
        db.execute("INSERT INTO analytics(youtube_id, fetched_at, views, likes, comments, avg_view_duration, "
                   "avg_view_percentage) VALUES (?,?,?,?,?,?,?)",
                   (vid, ts, s.get("views"), s.get("likes"), s.get("comments"), s.get("avg_view_duration"),
                    s.get("avg_view_percentage")))
    db.log("INFO", "analytics", f"영상 {len(stats)}개 성과 수집")
    return len(stats)
