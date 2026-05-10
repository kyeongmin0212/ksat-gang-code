#Requires -Version 5.1
# KsatGang daily analysis runner - for Task Scheduler.
# Steps: collector daily -> filter -> dante_strategy daily -> calculator
# - if internet down, wait 5 min then retry once
# - all output appended to logs\scheduler.log (UTF-8)
# - on python step failure, abort remaining steps

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

# Prevent Windows from sleeping while this script runs.
# 2026-05-07: a daily run died mid-step at 19:06 with no traceback (likely system sleep).
# Best-effort: any failure must NOT abort the analysis pipeline.
try {
    if (-not ([System.Management.Automation.PSTypeName]'Win32Sleep').Type) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class Win32Sleep {
    [DllImport("kernel32.dll")]
    public static extern uint SetThreadExecutionState(uint esFlags);
}
"@
    }
    # ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001) = 2147483649
    # Decimal literal avoids PS 5.1's signed-Int32 interpretation of 0x80000001.
    [Win32Sleep]::SetThreadExecutionState([uint32]2147483649) | Out-Null
} catch {
    Write-Host ("[warn] sleep prevention setup failed: " + $_.Exception.Message)
}

function Write-Log {
    param([string]$Msg)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [analysis] $Msg"
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
Write-Log "============== START daily analysis =============="

# Weekend skip (KRX closed on Sat/Sun); holidays handled inside collector.py
$dow = (Get-Date).DayOfWeek
if ($dow -eq [System.DayOfWeek]::Saturday) {
    Write-Log "SKIP — 토요일, KRX 휴장"
    Write-Log "============== END (skipped) =============="
    exit 0
}
if ($dow -eq [System.DayOfWeek]::Sunday) {
    Write-Log "SKIP — 일요일, KRX 휴장"
    Write-Log "============== END (skipped) =============="
    exit 0
}

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

# Stale-checkpoint guard: a previous-day incomplete indicator_checkpoint.json could
# be ressumed by today's run and silently skip 1000+ tickers (the 2026-05-07 incident).
# Today = yyyyMMdd (KRX 연동값과 같은 형식). dante_strategy 내부에도 같은 가드가 있지만
# 1차 방어선으로 여기서도 검사 후 백업한다.
$cpPath = Join-Path $BaseDir "indicator_checkpoint.json"
if (Test-Path $cpPath) {
    try {
        $cp = Get-Content $cpPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $cpDate = [string]$cp.base_date
        $today  = (Get-Date).ToString("yyyyMMdd")
        $done   = if ($cp.completed_tickers) { @($cp.completed_tickers).Count } else { 0 }
        $total  = if ($cp.total_tickers)     { [int]$cp.total_tickers }         else { 0 }
        $incomplete = ($total -gt 0) -and ($done -lt $total)
        $stale = $incomplete -and $cpDate -and ($cpDate -lt $today)
        if ($stale) {
            $bak = "$cpPath.stale_" + (Get-Date -Format "yyyyMMdd_HHmmss")
            Move-Item -Force -Path $cpPath -Destination $bak
            Write-Log ("[guard] stale checkpoint detected (base_date=$cpDate, $done/$total) — backed up to " + (Split-Path -Leaf $bak))
        }
    } catch {
        Write-Log ("[guard] checkpoint inspection failed: " + $_.Exception.Message + " — leaving as-is")
    }
}

$steps = @(
    @{ Name = "collector daily";      Args = @("collector.py", "daily") },
    @{ Name = "filter";               Args = @("filter.py") },
    @{ Name = "dante_strategy daily"; Args = @("dante_strategy.py", "--mode", "daily") },
    @{ Name = "calculator";           Args = @("calculator.py") }
)

foreach ($step in $steps) {
    Write-Log ("[step] " + $step.Name)
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    $rc = Invoke-Native -Exe $PythonExe -ArgList $step.Args -LogFile $LogFile

    $sw.Stop()
    if ($rc -ne 0) {
        Write-Log ("[step FAIL] " + $step.Name + " rc=$rc (" + [int]$sw.Elapsed.TotalSeconds + "s) - aborting")
        Write-Log "============== ABORTED =============="
        exit $rc
    }
    Write-Log ("[step OK] " + $step.Name + " (" + [int]$sw.Elapsed.TotalSeconds + "s)")
}

# --- Convert latest JSON to MD + refresh README (best-effort) ---
try {
    & $PythonExe (Join-Path $BaseDir "scripts\json_to_md.py") --latest 2>&1 | Out-Null
    & $PythonExe (Join-Path $BaseDir "scripts\generate_readme.py") 2>&1 | Out-Null
    Write-Log "[md] generated"
} catch {
    Write-Log ("[md] conversion failed: " + $_.Exception.Message + " — continuing")
}

# --- Auto-backup history/ to GitHub (best-effort; never fails the analysis run) ---
try {
    $historyDir = Join-Path $BaseDir "history"
    if (Test-Path (Join-Path $historyDir ".git")) {
        # Stage everything (gitignore handles *.bak_*, *.tmp, etc.)
        & git -C $historyDir add . 2>&1 | Out-Null
        $pending = & git -C $historyDir status --porcelain 2>&1
        if ([string]::IsNullOrWhiteSpace([string]::Join("`n", @($pending)))) {
            Write-Log "[github] no history changes — skip"
        } else {
            $msg = "Auto: " + (Get-Date -Format "yyyy-MM-dd HH:mm")
            & git -C $historyDir commit -q -m $msg 2>&1 | Out-Null
            $pushOut = & git -C $historyDir push origin main 2>&1
            $pushRc = $LASTEXITCODE
            if ($pushRc -eq 0) {
                Write-Log ("[github] push OK ($msg)")
            } else {
                Write-Log ("[github] push FAILED rc=$pushRc — local commit retained, will retry next run")
                $pushOut | ForEach-Object { Add-Content -Path $LogFile -Value ("    " + $_) -Encoding UTF8 }
            }
        }
    } else {
        Write-Log "[github] history/.git missing — skip"
    }
} catch {
    Write-Log ("[github] backup error: " + $_.Exception.Message + " — analysis still considered successful")
}

Write-Log "============== END daily analysis =============="
exit 0
