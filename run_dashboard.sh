#!/usr/bin/env bash
set -a
source /root/.config/mcbuildai.env
set +a
cd /root/mcbuildai
exec .venv/bin/python -m mcbuildai dashboard
