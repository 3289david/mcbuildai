@echo off
rem 스케줄러 + 작업자 + 대시보드 전체 실행
cd /d %~dp0
chcp 65001 >nul
set PYTHONUTF8=1
.venv\Scripts\python.exe -m mcbuildai run
pause
