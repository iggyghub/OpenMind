# Verify the OpenClaw auto-start chain end-to-end. Run AFTER you reboot.
#
# Checks (matches issue #162 acceptance criteria):
#   1. OpenClaw is listening on :3000 after boot (auto-start worked).
#   2. Cerebral connects: log line "[bridge] Connected to OpenClaw..."
#   3. With OpenClaw stopped, Cerebral still boots and logs the graceful
#      "[bridge] OpenClaw unreachable ... -- channel bridge disabled" warning.
#
# Side effects: spawns two short-lived Cerebral instances (up to 60s each)
# and kills them. Leaves OpenClaw stopped at the end -- restart with
# `openclaw start` or reboot.
#
# Safe to double-click -- pauses at the end so you can read the result.

$ErrorActionPreference = "Stop"

# Run from repo root so cerebral.main finds its config files.
Set-Location (Join-Path $PSScriptRoot "..")

function Write-Step($n, $msg) {
    Write-Host ""
    Write-Host "=== Step $n : $msg ===" -ForegroundColor Cyan
}
function Pass($msg) { Write-Host "PASS: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red }

function Test-OpenClawPort {
    $conn = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

function Wait-ForLogLine($logPaths, $pattern, $timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        foreach ($p in $logPaths) {
            if (Test-Path $p) {
                $hit = Select-String -Path $p -Pattern $pattern -SimpleMatch -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($hit) { return $hit.Line.Trim() }
            }
        }
        Start-Sleep -Milliseconds 500
    }
    return $null
}

function Start-Cerebral($logPath) {
    if (Test-Path $logPath) { Remove-Item $logPath -Force }
    if (Test-Path "$logPath.err") { Remove-Item "$logPath.err" -Force }
    return Start-Process -FilePath "python" `
        -ArgumentList "-m","cerebral.main" `
        -RedirectStandardOutput $logPath `
        -RedirectStandardError "$logPath.err" `
        -NoNewWindow -PassThru
}

function Stop-Cerebral($proc) {
    if ($proc -and -not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    }
}

function Dump-Tail($logPath) {
    Write-Host "--- $logPath (last 30 lines) ---"
    Get-Content $logPath -Tail 30 -ErrorAction SilentlyContinue
    if (Test-Path "$logPath.err") {
        Write-Host "--- $logPath.err (last 30 lines) ---"
        Get-Content "$logPath.err" -Tail 30 -ErrorAction SilentlyContinue
    }
}

$overallResult = "FAILED"
$evidence = @()

try {
    # Pre-flight -----------------------------------------------------
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        throw "python not found on PATH. Activate your venv first."
    }
    $alreadyRunning = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*cerebral.main*" }
    if ($alreadyRunning) {
        throw "Cerebral is already running (PID $($alreadyRunning.ProcessId)). Stop it before verifying."
    }

    # Step 1 ---------------------------------------------------------
    Write-Step 1 "OpenClaw listening on :3000 (auto-started on login)?"
    if (Test-OpenClawPort) {
        Pass "Something is listening on localhost:3000"
        $evidence += "step 1: port :3000 listening at boot (OpenClaw auto-started)"
    } else {
        Fail "Nothing listening on :3000. Did you reboot after running install-openclaw-autostart.ps1?"
        throw "Step 1 failed"
    }

    # Step 2 ---------------------------------------------------------
    Write-Step 2 "Cerebral connects to OpenClaw"
    $logA = Join-Path $env:TEMP "verify-openclaw-A.log"
    $cerebralA = Start-Cerebral $logA
    Write-Host "Cerebral PID $($cerebralA.Id). Waiting up to 60s for the bridge line..."
    $bridgeLine = Wait-ForLogLine @($logA, "$logA.err") "Connected to OpenClaw" 60
    Stop-Cerebral $cerebralA
    if ($bridgeLine) {
        Pass $bridgeLine
        $evidence += "step 2: $bridgeLine"
    } else {
        Fail "Bridge-connected line not seen in 60s."
        Dump-Tail $logA
        throw "Step 2 failed"
    }

    # Step 3 ---------------------------------------------------------
    Write-Step 3 "Graceful warning when OpenClaw is unreachable"
    Write-Host "Stopping the process listening on :3000..."
    $listeners = Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue
    foreach ($l in $listeners) {
        Stop-Process -Id $l.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    if (Test-OpenClawPort) {
        throw "Could not stop OpenClaw - port 3000 still listening."
    }
    $logB = Join-Path $env:TEMP "verify-openclaw-B.log"
    $cerebralB = Start-Cerebral $logB
    Write-Host "Cerebral PID $($cerebralB.Id). Waiting up to 30s for the warning line..."
    $warningLine = Wait-ForLogLine @($logB, "$logB.err") "OpenClaw unreachable" 30
    Stop-Cerebral $cerebralB
    if ($warningLine) {
        Pass $warningLine
        $evidence += "step 3: $warningLine"
    } else {
        Fail "Graceful-warning line not seen in 30s."
        Dump-Tail $logB
        throw "Step 3 failed"
    }

    $overallResult = "PASSED"
}
catch {
    Write-Host ""
    Write-Host "ERROR: $_" -ForegroundColor Red
}
finally {
    Write-Host ""
    if ($overallResult -eq "PASSED") {
        Write-Host "============================================" -ForegroundColor Green
        Write-Host " ALL THREE CHECKS PASSED" -ForegroundColor Green
        Write-Host "============================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Evidence to paste into issue #162:" -ForegroundColor Cyan
        foreach ($line in $evidence) { Write-Host "  $line" }
    } else {
        Write-Host "============================================" -ForegroundColor Red
        Write-Host " VERIFICATION DID NOT COMPLETE" -ForegroundColor Red
        Write-Host "============================================" -ForegroundColor Red
        if ($evidence.Count -gt 0) {
            Write-Host ""
            Write-Host "Partial evidence (steps that did pass):" -ForegroundColor Yellow
            foreach ($line in $evidence) { Write-Host "  $line" }
        }
    }
    Write-Host ""
    Write-Host "OpenClaw is currently STOPPED (or never started). Restart with:" -ForegroundColor Yellow
    Write-Host "  openclaw start"
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}
