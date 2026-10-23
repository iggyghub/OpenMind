# setup-shotcut.ps1 -- Install and verify Shotcut for video projects (ADR-0041).
#
# Run once: double-click from Explorer, or from a terminal.
# -NoPause: skip the "Press Enter" prompt (CI / chaining use).
#
# What it does:
#   1. Checks standard install dirs + PATH for shotcut.exe.
#   2. If absent, installs via winget (Meltytech.Shotcut), machine-wide.
#   3. Verifies shotcut.exe plus melt.exe (the render engine Felix uses) and
#      prints the melt version.
#
# CLAUDE.md rules: ASCII-only body, try/catch/finally pause, SUCCESS/FAILED markers.

param([switch]$NoPause)

$ErrorActionPreference = "Stop"

$STANDARD_DIRS = @(
    "C:\Program Files\Shotcut",
    "C:\Program Files (x86)\Shotcut"
)

function Find-Shotcut {
    foreach ($dir in $STANDARD_DIRS) {
        $p = Join-Path $dir "shotcut.exe"
        if (Test-Path $p) { return $p }
    }
    $inPath = Get-Command shotcut.exe -ErrorAction SilentlyContinue
    if ($inPath) { return $inPath.Source }
    return $null
}

$allPassed = $true

try {
    Write-Host ""
    Write-Host "=== OpenMind -- Shotcut setup ===" -ForegroundColor Cyan
    Write-Host ""

    # ----------------------------------------------------------------
    # Step 1: locate existing install
    # ----------------------------------------------------------------
    Write-Host "--- Checking for existing shotcut.exe ---"
    $shotcut = Find-Shotcut

    if ($shotcut) {
        Write-Host "Found: $shotcut" -ForegroundColor Green
    } else {
        Write-Host "shotcut.exe not found in standard dirs or PATH." -ForegroundColor Yellow
        Write-Host ""

        # ----------------------------------------------------------------
        # Step 2: install via winget
        # ----------------------------------------------------------------
        Write-Host "--- Installing Shotcut via winget ---"
        Write-Host "(About 200 MB. Windows may ask for administrator approval.)"
        Write-Host ""

        winget install --id Meltytech.Shotcut -e --scope machine --silent --accept-package-agreements --accept-source-agreements

        Write-Host ""
        Write-Host "winget install completed. Re-checking for shotcut.exe..."
        $shotcut = Find-Shotcut
    }

    # ----------------------------------------------------------------
    # Step 3: verify
    # ----------------------------------------------------------------
    Write-Host ""
    Write-Host "--- Verifying Shotcut ---"

    $melt = $null
    if ($shotcut) {
        $m = Join-Path (Split-Path $shotcut -Parent) "melt.exe"
        if (Test-Path $m) { $melt = $m }
    }

    if (-not $shotcut) {
        Write-Host "FAILED -- shotcut.exe not found after install attempt." -ForegroundColor Red
        Write-Host "Check that winget succeeded and that Shotcut is in a standard location."
        $allPassed = $false
    } elseif (-not $melt) {
        Write-Host "FAILED -- melt.exe not found next to $shotcut." -ForegroundColor Red
        Write-Host "Felix renders video projects with melt; reinstall Shotcut."
        $allPassed = $false
    } else {
        Write-Host "shotcut.exe path: $shotcut" -ForegroundColor Green
        Write-Host "melt.exe path:    $melt" -ForegroundColor Green

        try {
            $ver = & $melt --version 2>&1 | Select-Object -First 1
            Write-Host "melt version: $ver" -ForegroundColor Green
        } catch {
            Write-Host "WARNING: could not read melt version: $_" -ForegroundColor Yellow
        }

        Write-Host ""
        Write-Host "SUCCESS -- Shotcut is ready. Felix can now build and render video projects." -ForegroundColor Green
    }

} catch {
    Write-Host ""
    Write-Host "FAILED -- unexpected error: $_" -ForegroundColor Red
    $allPassed = $false
} finally {
    Write-Host ""
    if (-not $NoPause) {
        Read-Host "Press Enter to close" | Out-Null
    }
}

if (-not $allPassed) { exit 1 }
