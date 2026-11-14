#!/usr/bin/env bash
# systemd 사용자 서비스로 등록. 화면 녹화가 필요하므로 그래픽 로그인 세션(X11)에서 실행된다.
set -euo pipefail
dir="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$HOME/.config/systemd/user"
sed "s#__DIR__#$dir#g" "$dir/mcbuildai.service" > "$HOME/.config/systemd/user/mcbuildai.service"
systemctl --user daemon-reload
systemctl --user enable --now mcbuildai.service
echo "등록 완료: systemctl --user status mcbuildai"
