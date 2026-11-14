@echo off
rem Paper 서버만 따로 실행 (server.properties 를 RCON/평지 월드로 자동 설정)
cd /d %~dp0
chcp 65001 >nul
set PYTHONUTF8=1
.venv\Scripts\python.exe -m mcbuildai server start
pause
