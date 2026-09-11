# stop-strategy-repair.ps1 -- graceful stop for the Strategy Repair campaign loop.
# Drops a STOP file the running run-strategy-repair.ps1 checks between steps.
try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $stopFile = Join-Path $repoRoot ".claude\tmp\strategy-repair-loop\STOP"
    New-Item -ItemType Directory -Force -Path (Split-Path $stopFile) | Out-Null
    New-Item -ItemType File -Force -Path $stopFile | Out-Null
    Write-Host "STOP file created. The loop will end after the current step." -ForegroundColor Yellow
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
