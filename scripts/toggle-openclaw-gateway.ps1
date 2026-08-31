# Toggle the OpenClaw Gateway scheduled task fully on or off.
#
# The task has restart-on-failure configured (5 retries, 1 min apart,
# StartWhenAvailable) so it self-heals if the gateway process dies
# unexpectedly. That same setting fights a plain `Stop-ScheduledTask` or
# `openclaw daemon stop` -- Task Scheduler sees the process end and just
# restarts it within a minute, "stopped" or not.
#
# A DISABLED task never runs and never restarts -- that's the only state
# the restart-on-failure logic can't override. So "off" here means stop
# the running instance AND disable the task; "on" means re-enable it and
# start it immediately (rather than waiting for next login).
#
# Safe to double-click -- pauses at the end so you can read the result.

$ErrorActionPreference = "Stop"

function Pass($msg) { Write-Host "PASS: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red }

$TASK_NAME = "OpenClaw Gateway"
$PORT = 18789

$overallResult = "FAILED"

try {
    $task = Get-ScheduledTask -TaskName $TASK_NAME

    if ($task.State -ne "Disabled") {
        Write-Host ""
        Write-Host "=== Currently ON -- turning the OpenClaw Gateway OFF ===" -ForegroundColor Cyan
        Stop-ScheduledTask -TaskName $TASK_NAME
        Disable-ScheduledTask -TaskName $TASK_NAME | Out-Null
        Start-Sleep -Seconds 2
        $conn = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
        if (-not $conn) {
            Pass "Gateway stopped and task disabled -- it will NOT auto-restart (crash or manual stop) until toggled back on."
            $overallResult = "PASSED"
        } else {
            Fail "Task disabled but something is still listening on :$PORT -- check for a second process."
        }
    } else {
        Write-Host ""
        Write-Host "=== Currently OFF -- turning the OpenClaw Gateway ON ===" -ForegroundColor Cyan
        Enable-ScheduledTask -TaskName $TASK_NAME | Out-Null
        Start-ScheduledTask -TaskName $TASK_NAME
        # Observed taking up to ~13s to bind in practice -- poll instead of
        # a single fixed sleep so a slow-but-fine start doesn't false-FAIL.
        $conn = $null
        for ($i = 0; $i -lt 6; $i++) {
            Start-Sleep -Seconds 3
            $conn = Get-NetTCPConnection -LocalPort $PORT -State Listen -ErrorAction SilentlyContinue
            if ($conn) { break }
        }
        if ($conn) {
            Pass "Gateway task enabled and listening on :$PORT -- will auto-restart on crash and start at next login."
            $overallResult = "PASSED"
        } else {
            Fail "Task enabled and started but nothing is listening on :$PORT yet -- it may still be binding, re-check in a few seconds."
        }
    }
}
catch {
    Write-Host ""
    Write-Host "ERROR: $_" -ForegroundColor Red
}
finally {
    Write-Host ""
    if ($overallResult -eq "PASSED") {
        Write-Host "============================================" -ForegroundColor Green
        Write-Host " TOGGLE SUCCEEDED" -ForegroundColor Green
        Write-Host "============================================" -ForegroundColor Green
    } else {
        Write-Host "============================================" -ForegroundColor Red
        Write-Host " TOGGLE FAILED" -ForegroundColor Red
        Write-Host "============================================" -ForegroundColor Red
    }
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}
