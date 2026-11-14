@echo off
cd /d %~dp0
chcp 65001 >nul
set PYTHONUTF8=1
start "" http://127.0.0.1:8765
.venv\Scripts\python.exe -m mcbuildai dashboard
