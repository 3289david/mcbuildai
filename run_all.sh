#!/usr/bin/env bash
set -a
source /root/.config/mcbuildai.env
set +a
export DISPLAY=:99
cd /root/mcbuildai
exec .venv/bin/python -m mcbuildai run
