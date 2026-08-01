# stop-computer-use.ps1 -- graceful stop for the Computer-use loop.
#
# Creates the STOP sentinel file. The loop checks for it before each slice
# and each attempt, finishes the step currently in flight, then exits.
# To stop IMMEDIATELY instead, just close the loop's console window.

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $stateDir = Join-Path $repoRoot ".claude\tmp\computer-use-loop"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    New-Item -ItemType File -Force -Path (Join-Path $stateDir "STOP") | Out-Null
    Write-Host "STOP requested. The loop will exit after the current step finishes." -ForegroundColor Green
    Write-Host "(To stop immediately, close the loop's console window.)"
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
