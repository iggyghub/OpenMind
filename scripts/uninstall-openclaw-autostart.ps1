# Remove the OpenClaw Startup-folder shortcut created by
# install-openclaw-autostart.ps1.
#
# Safe to double-click from Explorer -- pauses at the end so you can read
# the output before the window closes.

$ErrorActionPreference = "Stop"

try {
    $startup = [Environment]::GetFolderPath("Startup")
    $lnkPath = Join-Path $startup "OpenClaw.lnk"

    if (Test-Path $lnkPath) {
        Remove-Item $lnkPath
        Write-Host ""
        Write-Host "REMOVED: $lnkPath" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "Nothing to remove - no shortcut at: $lnkPath" -ForegroundColor Yellow
    }
}
catch {
    Write-Host ""
    Write-Host "FAILED: $_" -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}
