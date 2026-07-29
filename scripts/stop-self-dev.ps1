# stop-self-dev.ps1 -- graceful stop for the self-dev-loop runner.
#
# Drops a STOP file the running run-self-dev.ps1 checks between steps (and during
# a usage-limit wait). The loop finishes any in-flight session, then exits cleanly
# instead of starting the next slice. Closing the loop's console window is the hard
# stop (kills the running step mid-flight).

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $stateDir = Join-Path $repoRoot ".claude\tmp\self-dev-loop"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $stopFile = Join-Path $stateDir "STOP"
    New-Item -ItemType File -Force -Path $stopFile | Out-Null
    Write-Host ("STOP file written: {0}" -f $stopFile) -ForegroundColor Green
    Write-Host "The loop will stop after the current slice finishes." -ForegroundColor Green
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
