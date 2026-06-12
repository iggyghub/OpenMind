# install-shortcut.ps1 -- one-shot Desktop + Start menu shortcut installer
# for the Felix launcher.
#
# Creates "Felix.lnk" pointing at scripts/launch-felix.ps1 with hidden-
# console PowerShell args. Run this once after cloning the repo; from
# then on, Felix is one double-click from the desktop forever.
#
# Safe to double-click from Explorer -- pause-on-exit so the user sees
# the result (per the CLAUDE.md operator-script gotcha).

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$launcher = Join-Path $repoRoot "scripts\launch-felix.ps1"

$desktop  = [Environment]::GetFolderPath("Desktop")
$startMnu = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"

$targets = @(
    (Join-Path $desktop  "Felix.lnk"),
    (Join-Path $startMnu "Felix.lnk")
)

$overallResult = "FAILED"
$created = @()

function Pass($msg) { Write-Host "PASS: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red }

try {
    Write-Host ""
    Write-Host "=== Installing Felix shortcuts ===" -ForegroundColor Cyan
    Write-Host "Launcher: $launcher"
    Write-Host ""

    if (-not (Test-Path $launcher)) {
        Fail "Launcher not found: $launcher"
        throw "Launcher missing -- did the repo layout change?"
    }

    $wsh = New-Object -ComObject WScript.Shell

    foreach ($lnk in $targets) {
        $parent = Split-Path -Parent $lnk
        if (-not (Test-Path $parent)) {
            Fail "Parent folder missing: $parent (skipping)"
            continue
        }

        $shortcut = $wsh.CreateShortcut($lnk)
        $shortcut.TargetPath = "powershell.exe"
        # -NoProfile keeps the launch fast (no $PROFILE dot-source).
        # -ExecutionPolicy Bypass lets the .ps1 run without per-machine
        #   Set-ExecutionPolicy ceremony.
        # -WindowStyle Hidden keeps the launcher console invisible; logs go
        #   to launcher.log in the repo root. Use "Show Logs" from the
        #   Felix tray menu to inspect them.
        # -File runs the script and exits, so the parent powershell.exe
        #   doesn't linger.
        $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
        $shortcut.WorkingDirectory = $repoRoot
        $shortcut.Description = "Launch Felix (OpenMind)"
        $shortcut.Save()

        Pass "Created: $lnk"
        $created += $lnk
    }

    if ($created.Count -eq 0) {
        throw "No shortcuts were created -- check folder permissions"
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
        Write-Host " FELIX SHORTCUTS INSTALLED" -ForegroundColor Green
        Write-Host "============================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Created:" -ForegroundColor Cyan
        foreach ($lnk in $created) { Write-Host "  $lnk" }
        Write-Host ""
        Write-Host "Double-click 'Felix' on your Desktop or pick it from the Start menu to launch."
    } else {
        Write-Host "============================================" -ForegroundColor Red
        Write-Host " SHORTCUT INSTALL FAILED" -ForegroundColor Red
        Write-Host "============================================" -ForegroundColor Red
    }
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}
