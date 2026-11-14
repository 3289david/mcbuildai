# mcbuildai Windows 설치 스크립트
# 실행: PowerShell 에서  powershell -ExecutionPolicy Bypass -File install.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Need($cmd, $hint) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { Write-Host "[필요] $cmd 를 찾을 수 없습니다. $hint" -ForegroundColor Yellow; return $false }
  return $true
}
$ok = $true
$ok = (Need "python" "https://www.python.org 에서 Python 3.11 이상 설치 (Add to PATH 체크)") -and $ok
$ok = (Need "ffmpeg" "winget install Gyan.FFmpeg 또는 https://www.gyan.dev/ffmpeg/builds/") -and $ok
$ok = (Need "java" "winget install EclipseAdoptium.Temurin.21.JDK (Paper 1.21 은 Java 21 필요)") -and $ok
if (-not $ok) { Write-Host "위 프로그램을 설치한 뒤 다시 실행하세요."; exit 1 }

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path "config.yaml")) { Copy-Item "config.windows.yaml" "config.yaml" }
.\.venv\Scripts\python.exe -m mcbuildai init
New-Item -ItemType Directory -Force -Path "server" | Out-Null
Write-Host ""
Write-Host "설치 완료. 다음 단계:" -ForegroundColor Green
Write-Host " 1) server 폴더에 Paper 서버 jar 를 paper.jar 이름으로 넣고, 한 번 실행해 eula.txt 에 eula=true 로 동의"
Write-Host " 2) config.yaml 에서 server.rcon_password, client.camera_player, client.launch_command 설정"
Write-Host " 3) setx ANTHROPIC_API_KEY ""<키>""  (genMC 설계와 AI 기획에 필요)"
Write-Host " 4) youtube_auth.bat 실행 (YouTube 계정 연결)"
Write-Host " 5) start_all.bat 실행 후 http://127.0.0.1:8765 접속"
