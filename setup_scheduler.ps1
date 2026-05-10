#Requires -Version 5.1
# KsatGang scheduler setup - registers 2 daily tasks.
#   1) KsatGang_Daily_Analysis  - 19:00 daily, data pipeline
#   2) KsatGang_Daily_Notify    - 08:30 daily, telegram notify
# Settings: only run when PC is on (no wake), skip missed runs, allow battery,
#           current user context (env vars TG_BOT_TOKEN/TG_CHAT_ID inherited).

$ErrorActionPreference = "Stop"
$BaseDir         = "C:\Users\sji48\ksat_gang"
$AnalysisRunner  = Join-Path $BaseDir "runner_analysis.ps1"
$NotifyRunner    = Join-Path $BaseDir "runner_notify.ps1"

# Pre-check runner files
foreach ($f in @($AnalysisRunner, $NotifyRunner)) {
    if (-not (Test-Path $f)) {
        Write-Host "[ERR] runner not found: $f"
        exit 1
    }
}

# Common settings - run only when PC is on
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable:$false `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew

# Reinforce: do not wake the computer
$settings.WakeToRun = $false
$settings.DisallowStartIfOnBatteries = $false

# Current user, interactive logon, normal user privileges
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

# --- Task 1: Daily Analysis at 19:00 ---
$action1 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$AnalysisRunner`"" `
    -WorkingDirectory $BaseDir
$trigger1 = New-ScheduledTaskTrigger -Daily -At "19:00"

Register-ScheduledTask `
    -TaskName "KsatGang_Daily_Analysis" `
    -Description "Ksat Gang - daily data pipeline (collector/filter/strategy/calculator)" `
    -Action $action1 `
    -Trigger $trigger1 `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null
Write-Host "[OK] KsatGang_Daily_Analysis registered (daily 19:00)"

# --- Task 2: Daily Notify at 08:30 ---
$action2 = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$NotifyRunner`"" `
    -WorkingDirectory $BaseDir
$trigger2 = New-ScheduledTaskTrigger -Daily -At "08:30"

Register-ScheduledTask `
    -TaskName "KsatGang_Daily_Notify" `
    -Description "Ksat Gang - daily telegram notify" `
    -Action $action2 `
    -Trigger $trigger2 `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null
Write-Host "[OK] KsatGang_Daily_Notify registered (daily 08:30)"

Write-Host ""
Write-Host "--- Registered tasks ---"
Get-ScheduledTask -TaskName "KsatGang_*" |
    Get-ScheduledTaskInfo |
    Select-Object TaskName, NextRunTime, LastRunTime, LastTaskResult |
    Format-Table -AutoSize

Write-Host "--- Test now (manual) ---"
Write-Host "  Start-ScheduledTask -TaskName 'KsatGang_Daily_Analysis'"
Write-Host "  Start-ScheduledTask -TaskName 'KsatGang_Daily_Notify'"
Write-Host ""
Write-Host "--- Cancel / remove ---"
Write-Host "  Unregister-ScheduledTask -TaskName 'KsatGang_Daily_Analysis' -Confirm:`$false"
Write-Host "  Unregister-ScheduledTask -TaskName 'KsatGang_Daily_Notify'   -Confirm:`$false"
