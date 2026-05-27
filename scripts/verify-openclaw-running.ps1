# Verify the OpenClaw gateway is running after login.
#
# Closes issue #162 AC1 ("OpenClaw gateway auto-starts on login"). Run this
# AFTER you reboot, with no manual `openclaw gateway start` in between.
#
# Cerebral-side connection / inbound message live-verify is NOT in scope here
# -- that moved to issue #167 (bridge update for OpenClaw 2026.4.29 API drift).
#
# Safe to double-click -- pauses at the end so you can read the result.

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

function Pass($msg) { Write-Host "PASS: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red }

$OPENCLAW_PORT = 18789

function Test-OpenClawPort {
    $conn = Get-NetTCPConnection -LocalPort $OPENCLAW_PORT -State Listen -ErrorAction SilentlyContinue
    return [bool]$conn
}

$overallResult = "FAILED"
$evidence = @()

try {
    Write-Host ""
    Write-Host "=== OpenClaw gateway listening on :$OPENCLAW_PORT (auto-started on login)? ===" -ForegroundColor Cyan

    # `openclaw gateway status` is racy right after a fresh boot -- the runtime
    # field can read "stopped" while the listener is still binding. Re-check
    # after a short pause before we declare failure.
    $listening = Test-OpenClawPort
    if (-not $listening) {
        Write-Host "Nothing on :$OPENCLAW_PORT yet, waiting 5s in case the gateway is still binding..."
        Start-Sleep -Seconds 5
        $listening = Test-OpenClawPort
    }

    if ($listening) {
        $proc = Get-NetTCPConnection -LocalPort $OPENCLAW_PORT -State Listen |
            Select-Object -First 1 |
            ForEach-Object { Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue }
        $procName = if ($proc) { "$($proc.ProcessName) (PID $($proc.Id))" } else { "unknown process" }
        Pass "127.0.0.1:$OPENCLAW_PORT is being listened on by $procName"
        $evidence += "127.0.0.1:$OPENCLAW_PORT listening at boot via $procName"
    } else {
        Fail "Nothing listening on :$OPENCLAW_PORT after 5s. Did you run ``openclaw gateway install`` + ``openclaw config set gateway.mode local`` + ``openclaw gateway start`` and then reboot?"
        throw "OpenClaw gateway is not running"
    }

    # Also surface the gateway's own status output as supplementary evidence.
    if (Get-Command openclaw -ErrorAction SilentlyContinue) {
        Write-Host ""
        Write-Host "--- openclaw gateway status ---"
        try {
            $status = & openclaw gateway status 2>&1
            $status | ForEach-Object { Write-Host $_ }
            $evidence += "openclaw gateway status output captured above"
        } catch {
            Write-Host "(gateway status command failed: $_)" -ForegroundColor Yellow
        }
    } else {
        Write-Host "(openclaw CLI not on PATH -- skipping gateway status)" -ForegroundColor Yellow
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
        Write-Host " OPENCLAW GATEWAY IS RUNNING POST-BOOT" -ForegroundColor Green
        Write-Host "============================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Evidence to paste into issue #162:" -ForegroundColor Cyan
        foreach ($line in $evidence) { Write-Host "  $line" }
    } else {
        Write-Host "============================================" -ForegroundColor Red
        Write-Host " VERIFICATION FAILED" -ForegroundColor Red
        Write-Host "============================================" -ForegroundColor Red
        if ($evidence.Count -gt 0) {
            Write-Host ""
            Write-Host "Partial evidence:" -ForegroundColor Yellow
            foreach ($line in $evidence) { Write-Host "  $line" }
        }
    }
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}
