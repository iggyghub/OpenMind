# Remove the OpenClaw Startup-folder shortcut created by
# install-openclaw-autostart.ps1.

$ErrorActionPreference = "Stop"

$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "OpenClaw.lnk"

if (Test-Path $lnkPath) {
    Remove-Item $lnkPath
    Write-Host "Removed: $lnkPath"
} else {
    Write-Host "No OpenClaw startup shortcut found at $lnkPath"
}
