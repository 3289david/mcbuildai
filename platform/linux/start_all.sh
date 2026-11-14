#!/usr/bin/env bash
cd "$(dirname "$0")"
export DISPLAY="${DISPLAY:-:0}"
[ -f "$HOME/.config/mcbuildai.env" ] && set -a && . "$HOME/.config/mcbuildai.env" && set +a
exec .venv/bin/python -m mcbuildai run
