# Windows 작업 스케줄러에 매일 자동 실행 등록
# 사용: 관리자 PowerShell에서  .\register_task.ps1  실행
# 기본 매일 16:30 (장마감 15:30 이후) 실행. 시간 변경하려면 -Time 파라미터.

param(
    [string]$Time = "16:30",
    [string]$TaskName = "EconYoutubeDashboard"
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$runBat = Join-Path $scriptDir "run.bat"

if (-not (Test-Path $runBat)) {
    Write-Error "run.bat 를 찾을 수 없습니다: $runBat"
    exit 1
}

$action  = New-ScheduledTaskAction -Execute $runBat -WorkingDirectory $scriptDir
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3)

# 기존 동일 이름 태스크 있으면 교체
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description "매일 경제 유튜브 분석 → 대시보드 생성" `
    -RunLevel Limited | Out-Null

Write-Host "등록 완료: '$TaskName' 매일 $Time 실행"
Write-Host "확인:  Get-ScheduledTask -TaskName $TaskName"
Write-Host "수동 실행:  Start-ScheduledTask -TaskName $TaskName"
