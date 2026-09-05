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

$launcher    = Join-Path $repoRoot "scripts\launch-felix.ps1"
$vbsLauncher = Join-Path $repoRoot "scripts\launch-felix-hidden.vbs"

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
    if (-not (Test-Path $vbsLauncher)) {
        Fail "Silent-launch wrapper not found: $vbsLauncher"
        throw "Wrapper missing -- did the repo layout change?"
    }

    $wsh = New-Object -ComObject WScript.Shell

    foreach ($lnk in $targets) {
        $parent = Split-Path -Parent $lnk
        if (-not (Test-Path $parent)) {
            Fail "Parent folder missing: $parent (skipping)"
            continue
        }

        $shortcut = $wsh.CreateShortcut($lnk)
        # Targets the .vbs wrapper, not powershell.exe directly: passing
        # -WindowStyle Hidden to powershell.exe still lets Windows create and
        # briefly flash a console window before PowerShell's own startup code
        # gets around to applying that flag. launch-felix-hidden.vbs uses
        # WshShell.Run's window-style parameter (0 = SW_HIDE), which the OS
        # applies at process-creation time instead -- no window ever appears.
        # Logs still land in launcher.log in the repo root regardless; use
        # "Show Logs" from the Felix tray menu to inspect them.
        $shortcut.TargetPath = $vbsLauncher
        $shortcut.Arguments = ""
        $shortcut.WorkingDirectory = $repoRoot
        $shortcut.Description = "Launch Felix (OpenMind)"
        $shortcut.IconLocation = (Join-Path $repoRoot "tray\assets\icon.ico") + ",0"
        $shortcut.Save()

        # Must match tray/main.js's app.setAppUserModelID call -- otherwise this
        # shortcut and the Electron window it launches get different Windows
        # taskbar identities and show as two separate icons instead of merging
        # into one (the pin, and pinning it to the taskbar later both inherit
        # this from the shortcut file itself, so this only needs to run once).
        try {
            & (Join-Path $repoRoot "scripts\set-shortcut-appid.ps1") -Path $lnk -AppId "OpenMind.Felix"
        } catch {
            Fail "Could not set AppUserModelID on $lnk (non-fatal -- shortcut still works, just won't merge with the taskbar icon): $_"
        }

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
