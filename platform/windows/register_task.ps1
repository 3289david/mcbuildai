# 로그인 시 mcbuildai 를 자동 실행하도록 작업 스케줄러에 등록
# Minecraft 화면을 녹화해야 하므로 '사용자가 로그온할 때만 실행' 으로 등록한다 (화면이 필요).
$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "$dir\start_all.bat" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 2) -ExecutionTimeLimit (New-TimeSpan -Days 30)
Register-ScheduledTask -TaskName "mcbuildai" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Force
Write-Host "작업 스케줄러에 'mcbuildai' 등록 완료 (로그온 시 실행)"
