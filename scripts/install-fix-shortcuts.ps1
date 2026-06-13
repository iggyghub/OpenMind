# install-fix-shortcuts.ps1 -- one-shot Desktop shortcut installer for the
# run-and-fix loop (run-fixes.ps1 / stop-fixes.ps1).
#
# Mirrors the existing "Start Slice Loop" / "Stop Slice Loop" desktop shortcuts
# but for the run-and-fix loop. Creates:
#   "Start Fix Loop.lnk" -> scripts\run-fixes.ps1
#   "Stop Fix Loop.lnk"  -> scripts\stop-fixes.ps1
#
# Visible console (WindowStyle Normal) so you can watch the loop progress and
# dismiss its notifications -- NOT hidden like the Felix launcher.
#
# Safe to double-click from Explorer -- pause-on-exit per the CLAUDE.md
# operator-script gotcha.

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$desktop = [Environment]::GetFolderPath("Desktop")

# name -> target script (relative to repo root)
$shortcuts = @(
    @{ Name = "Start Fix Loop.lnk"; Script = "scripts\run-fixes.ps1";  Desc = "Start the OpenMind run-and-fix loop" },
    @{ Name = "Stop Fix Loop.lnk";  Script = "scripts\stop-fixes.ps1"; Desc = "Stop the OpenMind run-and-fix loop gracefully" }
)

$overallResult = "FAILED"
$created = @()

function Pass($msg) { Write-Host "PASS: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red }

try {
    Write-Host ""
    Write-Host "=== Installing Fix Loop shortcuts ===" -ForegroundColor Cyan
    Write-Host "Desktop: $desktop"
    Write-Host ""

    $wsh = New-Object -ComObject WScript.Shell

    foreach ($sc in $shortcuts) {
        $script = Join-Path $repoRoot $sc.Script
        if (-not (Test-Path $script)) {
            Fail ("Script not found: {0} (skipping {1})" -f $script, $sc.Name)
            continue
        }

        $lnk = Join-Path $desktop $sc.Name
        $shortcut = $wsh.CreateShortcut($lnk)
        $shortcut.TargetPath = "powershell.exe"
        # Matches the Start/Stop Slice Loop shortcuts: visible window, no profile,
        # bypass execution policy, -File runs the script and exits.
        $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
        $shortcut.WorkingDirectory = $repoRoot
        $shortcut.WindowStyle = 1   # 1 = Normal (visible). The loop console must be seen.
        $shortcut.Description = $sc.Desc
        $shortcut.Save()

        Pass ("Created: {0}" -f $lnk)
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
        Write-Host " FIX LOOP SHORTCUTS INSTALLED" -ForegroundColor Green
        Write-Host "============================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Created:" -ForegroundColor Cyan
        foreach ($lnk in $created) { Write-Host "  $lnk" }
        Write-Host ""
        Write-Host "Double-click 'Start Fix Loop' on your Desktop to run it; 'Stop Fix Loop' to halt gracefully."
    } else {
        Write-Host "============================================" -ForegroundColor Red
        Write-Host " SHORTCUT INSTALL FAILED" -ForegroundColor Red
        Write-Host "============================================" -ForegroundColor Red
    }
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}
