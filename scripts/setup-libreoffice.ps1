# setup-libreoffice.ps1 -- Install and verify LibreOffice for the Documents campaign.
#
# Run once: double-click from Explorer, or from a terminal.
# -NoPause: skip the "Press Enter" prompt (CI / chaining use).
#
# What it does:
#   1. Checks standard install dirs + PATH for soffice.exe.
#   2. If absent, installs via winget (TheDocumentFoundation.LibreOffice).
#   3. Verifies the install by finding soffice.exe and printing its version.
#
# CLAUDE.md rules: ASCII-only body, try/catch/finally pause, SUCCESS/FAILED markers.

param([switch]$NoPause)

$ErrorActionPreference = "Stop"

$STANDARD_DIRS = @(
    "C:\Program Files\LibreOffice\program",
    "C:\Program Files (x86)\LibreOffice\program"
)

function Find-Soffice {
    foreach ($dir in $STANDARD_DIRS) {
        $p = Join-Path $dir "soffice.exe"
        if (Test-Path $p) { return $p }
    }
    $inPath = Get-Command soffice.exe -ErrorAction SilentlyContinue
    if ($inPath) { return $inPath.Source }
    return $null
}

$allPassed = $true

try {
    Write-Host ""
    Write-Host "=== OpenMind -- LibreOffice setup ===" -ForegroundColor Cyan
    Write-Host ""

    # ----------------------------------------------------------------
    # Step 1: locate existing install
    # ----------------------------------------------------------------
    Write-Host "--- Checking for existing soffice.exe ---"
    $soffice = Find-Soffice

    if ($soffice) {
        Write-Host "Found: $soffice" -ForegroundColor Green
    } else {
        Write-Host "soffice.exe not found in standard dirs or PATH." -ForegroundColor Yellow
        Write-Host ""

        # ----------------------------------------------------------------
        # Step 2: install via winget
        # ----------------------------------------------------------------
        Write-Host "--- Installing LibreOffice via winget ---"
        Write-Host "(This may take several minutes and ~700 MB of download.)"
        Write-Host ""

        winget install --id TheDocumentFoundation.LibreOffice --silent --accept-package-agreements --accept-source-agreements

        Write-Host ""
        Write-Host "winget install completed. Re-checking for soffice.exe..."
        $soffice = Find-Soffice
    }

    # ----------------------------------------------------------------
    # Step 3: verify
    # ----------------------------------------------------------------
    Write-Host ""
    Write-Host "--- Verifying LibreOffice ---"

    if (-not $soffice) {
        Write-Host "FAILED -- soffice.exe not found after install attempt." -ForegroundColor Red
        Write-Host "Check that winget succeeded and that LibreOffice is in a standard location."
        $allPassed = $false
    } else {
        Write-Host "soffice.exe path: $soffice" -ForegroundColor Green

        # Get version (soffice --version writes to stdout)
        try {
            $ver = & $soffice --version 2>&1
            Write-Host "Version: $ver" -ForegroundColor Green
        } catch {
            Write-Host "WARNING: could not read version string: $_" -ForegroundColor Yellow
        }

        Write-Host ""
        Write-Host "SUCCESS -- LibreOffice is ready. Felix can now open, edit, and convert documents." -ForegroundColor Green
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
