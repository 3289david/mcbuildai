"""Prism Launcher 없이, Microsoft 기기 코드(device code) 흐름으로 바로 마인크래프트 계정을
연결한다. 결과를 Prism Launcher의 accounts.json 형식으로 직접 저장한다.
"""
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

CLIENT_ID = "c36a9fb6-4f2a-41ff-90bd-ae7cc92031eb"  # Prism Launcher 공개 Azure 앱 ID
ACCOUNTS_PATH = Path.home() / ".local/share/PrismLauncher/accounts.json"


def post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def post_json(url, obj, extra_headers=None):
    body = json.dumps(obj).encode()
    headers = {"Content-Type": "application/json", "Accept": "application/json", "x-xbl-contract-version": "1"}
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def main():
    # 1) 기기 코드 요청
    dev = post_form("https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode", {
        "client_id": CLIENT_ID, "scope": "XboxLive.SignIn XboxLive.offline_access",
    })
    print(f"\n>>> 아래 주소를 열고 코드를 입력해 로그인하세요:")
    print(f">>> {dev['verification_uri']}")
    print(f">>> 코드: {dev['user_code']}\n")

    interval = dev.get("interval", 5)
    deadline = time.time() + dev["expires_in"]
    ms_token = None
    while time.time() < deadline:
        time.sleep(interval)
        resp = post_form("https://login.microsoftonline.com/consumers/oauth2/v2.0/token", {
            "client_id": CLIENT_ID,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": dev["device_code"],
        })
        if resp.get("error") == "authorization_pending":
            print("...아직 로그인 안 함, 계속 기다림")
            continue
        if resp.get("error") == "slow_down":
            interval += 5
            continue
        if "error" in resp:
            raise SystemExit(f"로그인 실패: {resp}")
        ms_token = resp
        break
    if not ms_token:
        raise SystemExit("시간 초과. 다시 시도해주세요.")

    print(">>> Microsoft 로그인 완료. Xbox Live 인증 중...")

    # 2) Xbox Live user token
    xbl = post_json("https://user.auth.xboxlive.com/user/authenticate", {
        "Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": f"d={ms_token['access_token']}"},
        "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT",
    })
    uhs = xbl["DisplayClaims"]["xui"][0]["uhs"]

    # 3) XSTS token (Minecraft 전용)
    xsts = post_json("https://xsts.auth.xboxlive.com/xsts/authorize", {
        "Properties": {"SandboxId": "RETAIL", "UserTokens": [xbl["Token"]]},
        "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT",
    })

    print(">>> 마인크래프트 토큰 발급 중...")

    # 4) 마인크래프트 access token
    mc = post_json("https://api.minecraftservices.com/authentication/login_with_xbox", {
        "identityToken": f"XBL3.0 x={uhs};{xsts['Token']}",
    })

    # 5) 프로필 (유저네임/UUID)
    req = urllib.request.Request("https://api.minecraftservices.com/minecraft/profile",
                                 headers={"Authorization": f"Bearer {mc['access_token']}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        profile = json.loads(r.read())

    if "error" in profile or "id" not in profile:
        raise SystemExit(f"이 계정은 마인크래프트를 소유하지 않은 것 같습니다: {profile}")

    print(f">>> 연결됨: {profile['name']} ({profile['id']})")

    # Prism Launcher accounts.json 작성
    now = datetime.now(timezone.utc)
    account = {
        "type": "MSA",
        "msa-client-id": CLIENT_ID,
        "msa": {"iat": int(now.timestamp()), "exp": int((now + timedelta(seconds=ms_token["expires_in"])).timestamp()),
                "token": ms_token["access_token"], "refresh_token": ms_token["refresh_token"], "extra": ms_token},
        "utoken": {"iat": int(now.timestamp()), "token": xbl["Token"], "extra": {"uhs": uhs}},
        "xrp-mc": {"iat": int(now.timestamp()), "token": xsts["Token"], "extra": {"uhs": uhs}},
        "ygg": {"iat": int(now.timestamp()), "exp": int((now + timedelta(seconds=mc.get("expires_in", 86400))).timestamp()),
                "token": mc["access_token"]},
        "profile": {"id": profile["id"], "name": profile["name"]},
        "entitlement": {"ownsMinecraft": True, "canPlayMinecraft": True},
        "active": True,
        "id": profile["id"],
    }

    ACCOUNTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"formatVersion": 3, "accounts": [account]}
    if ACCOUNTS_PATH.exists():
        try:
            existing = json.loads(ACCOUNTS_PATH.read_text())
            others = [a for a in existing.get("accounts", []) if a.get("profile", {}).get("id") != profile["id"]]
            data["accounts"] = others + [account]
        except Exception:
            pass
    ACCOUNTS_PATH.write_text(json.dumps(data, indent=2))
    print(f">>> 저장 완료: {ACCOUNTS_PATH}")
    print(f">>> 유저네임: {profile['name']}")


if __name__ == "__main__":
    main()
