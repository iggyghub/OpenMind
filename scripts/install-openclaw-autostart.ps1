# Install a Startup-folder shortcut that launches OpenClaw on Windows login.
#
# Idempotent: re-running overwrites the existing shortcut. Requires `openclaw`
# to already be on PATH (`npm install -g openclaw@latest`). Pair with
# uninstall-openclaw-autostart.ps1 to remove.

$ErrorActionPreference = "Stop"

$openclawCmd = Get-Command openclaw -ErrorAction SilentlyContinue
if (-not $openclawCmd) {
    Write-Error "openclaw not found on PATH. Install it first: npm install -g openclaw@latest"
    exit 1
}

$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "OpenClaw.lnk"

$wsh = New-Object -ComObject WScript.Shell
$shortcut = $wsh.CreateShortcut($lnkPath)
$shortcut.TargetPath = "cmd.exe"
$shortcut.Arguments = "/c openclaw start"
$shortcut.WorkingDirectory = $env:USERPROFILE
$shortcut.WindowStyle = 7
$shortcut.Description = "Auto-start OpenClaw harness for Cerebral channel bridge"
$shortcut.Save()

Write-Host "Installed: $lnkPath"
Write-Host "OpenClaw will start minimised on next login."
Write-Host "Remove with: scripts\uninstall-openclaw-autostart.ps1"
