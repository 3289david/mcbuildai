#!/usr/bin/env bash
# mcbuildai Linux 설치 스크립트 (Ubuntu/Debian 기준)
set -euo pipefail
cd "$(dirname "$0")"
missing=()
for c in python3 ffmpeg java xdotool; do command -v "$c" >/dev/null || missing+=("$c"); done
if ((${#missing[@]})); then
  echo "[필요] 없는 프로그램: ${missing[*]}"
  echo "  sudo apt install python3 python3-venv ffmpeg openjdk-21-jre-headless xdotool fonts-noto-cjk"
  exit 1
fi
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
[ -f config.yaml ] || cp config.linux.yaml config.yaml
.venv/bin/python -m mcbuildai init
mkdir -p server
if [ "${XDG_SESSION_TYPE:-}" = "wayland" ]; then
  echo "[주의] Wayland 세션에서는 x11grab 화면 녹화가 동작하지 않습니다. X11(Xorg) 세션으로 로그인하세요."
fi
cat <<MSG

설치 완료. 다음 단계:
 1) server/ 에 Paper 서버 jar 를 paper.jar 로 넣고 한 번 실행해 eula.txt 에 eula=true 로 동의
 2) config.yaml 에서 server.rcon_password, client.camera_player, client.launch_command 설정
 3) export ANTHROPIC_API_KEY=<키>  (~/.config/mcbuildai.env 에 넣으면 systemd 서비스가 읽음)
 4) ./youtube_auth.sh  (YouTube 계정 연결)
 5) ./start_all.sh 실행 후 http://127.0.0.1:8765 접속
 6) 상시 운영: ./install_service.sh
MSG
