#Requires -Version 5.1
# KsatGang daily telegram notify runner - for Task Scheduler.
# - if internet down, wait 5 min then retry once
# - output appended to logs\scheduler.log (UTF-8)
# - requires env vars TG_BOT_TOKEN, TG_CHAT_ID

$ErrorActionPreference = "Stop"
# Helper: avoid PS 5.1 NativeCommandError trap when native exe (python.exe) writes to stderr
function Invoke-Native {
    param(
        [Parameter(Mandatory)] [string] $Exe,
        [Parameter(Mandatory)] [string[]] $ArgList,
        [Parameter(Mandatory)] [string] $LogFile
    )
    $prev = $script:ErrorActionPreference
    $script:ErrorActionPreference = "Continue"
    try {
        & $Exe @ArgList 2>&1 | ForEach-Object {
            Add-Content -Path $LogFile -Value ("    " + $_) -Encoding UTF8
        }
        return $LASTEXITCODE
    } finally {
        $script:ErrorActionPreference = $prev
    }
}

$BaseDir   = "C:\Users\sji48\ksat_gang"
$LogDir    = Join-Path $BaseDir "logs"
$LogFile   = Join-Path $LogDir "scheduler.log"
$PythonExe = "C:\Users\sji48\AppData\Local\Programs\Python\Python311\python.exe"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
}

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [notify] $Msg"
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Test-Internet {
    try {
        $ok = Test-Connection -ComputerName 8.8.8.8 -Count 1 -Quiet -ErrorAction Stop
        return [bool]$ok
    } catch {
        return $false
    }
}

Set-Location $BaseDir
Write-Log "============== START daily notify =============="

if (-not (Test-Internet)) {
    Write-Log "internet OFF - waiting 5 min then retry once"
    Start-Sleep -Seconds 300
    if (-not (Test-Internet)) {
        Write-Log "internet still OFF - abort"
        exit 1
    }
    Write-Log "internet recovered - continue"
} else {
    Write-Log "internet OK"
}

if (-not $env:TG_BOT_TOKEN -or -not $env:TG_CHAT_ID) {
    Write-Log "env TG_BOT_TOKEN/TG_CHAT_ID not set - abort"
    Write-Log "  -> set with: setx TG_BOT_TOKEN ... ; setx TG_CHAT_ID ..."
    exit 2
}

$sw = [System.Diagnostics.Stopwatch]::StartNew()
$rc = Invoke-Native -Exe $PythonExe -ArgList @("notifier.py") -LogFile $LogFile
$sw.Stop()

if ($rc -ne 0) {
    Write-Log ("notifier failed rc=$rc (" + [int]$sw.Elapsed.TotalSeconds + "s)")
    Write-Log "============== ABORTED =============="
    exit $rc
}

Write-Log ("notifier OK (" + [int]$sw.Elapsed.TotalSeconds + "s)")
Write-Log "============== END daily notify =============="
exit 0
