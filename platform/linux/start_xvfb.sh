#!/usr/bin/env bash
# 모니터 없는 서버용: 1080x1920 세로 가상 화면을 띄운다 (세로 영상을 크롭 없이 녹화).
# GPU 가속이 없으면 Minecraft 가 소프트웨어 렌더링(llvmpipe)으로 매우 느릴 수 있다.
# 사용 후 config.yaml: capture.width=1080, capture.height=1920, capture.display=":99.0"
set -euo pipefail
command -v Xvfb >/dev/null || { echo "sudo apt install xvfb"; exit 1; }
Xvfb :99 -screen 0 1080x1920x24 -nolisten tcp &
echo "DISPLAY=:99 준비됨 (pid $!)"
