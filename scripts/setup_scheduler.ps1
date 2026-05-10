# 작업 스케줄러 등록 스크립트
# 새 PC에서 시스템 복구 시 사용

$BaseDir = $PSScriptRoot | Split-Path -Parent

# KsatGang_Daily_Analysis
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$BaseDir\runner_analysis_silent.vbs`""
$trigger = New-ScheduledTaskTrigger -Daily -At 19:00
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -Hidden -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "KsatGang_Daily_Analysis" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force

# KsatGang_Daily_Notify
$action2 = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$BaseDir\runner_notify_silent.vbs`""
$trigger2 = New-ScheduledTaskTrigger -Daily -At 08:30
$settings2 = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -Hidden -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 5) -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
$principal2 = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
Register-ScheduledTask -TaskName "KsatGang_Daily_Notify" -Action $action2 -Trigger $trigger2 -Settings $settings2 -Principal $principal2 -Force

Write-Host "✅ 작업 스케줄러 등록 완료"
Write-Host "확인: Get-ScheduledTask -TaskName 'KsatGang*'"
