# Verify the automatable v1 pre-conditions before running the human checklist.
#
# Checks:
#   1. All six Google plugin files are present.
#   2. All five fallback tool registrations are present in google_workspace_fallback.py.
#   3. GOOGLE_MAPS_API_KEY env var is set (Maps plugin needs a static key -- no OAuth).
#   4. Local docs directory exists or can be created (Docs fallback writes here).
#   5. Cerebral main.py contains the full _GOOGLE_SCOPES list (docs/sheets/tasks/drive/contacts).
#
# Safe to double-click -- pauses at the end so you can read the result.
# Run from the repo root or from scripts/; the script normalises the working dir.

$ErrorActionPreference = "Stop"

Set-Location (Join-Path $PSScriptRoot "..")

function Pass($msg) { Write-Host "PASS: $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red }

$allPassed = $true

try {

    Write-Host ""
    Write-Host "=== OpenMind v1 pre-conditions ===" -ForegroundColor Cyan
    Write-Host ""

    # -----------------------------------------------------------------------
    # Check 1: Google plugin files
    # -----------------------------------------------------------------------
    Write-Host "--- 1. Google plugin files ---"
    $plugins = @(
        "plugins\google_docs.py",
        "plugins\google_sheets.py",
        "plugins\google_maps.py",
        "plugins\google_tasks.py",
        "plugins\google_drive.py",
        "plugins\google_contacts.py"
    )
    foreach ($f in $plugins) {
        if (Test-Path $f) {
            Pass $f
        } else {
            Fail "$f is missing"
            $allPassed = $false
        }
    }

    # -----------------------------------------------------------------------
    # Check 2: Fallback tool registrations
    # -----------------------------------------------------------------------
    Write-Host ""
    Write-Host "--- 2. Fallback registrations in google_workspace_fallback.py ---"
    $fallbackFile = "plugins\google_workspace_fallback.py"
    if (-not (Test-Path $fallbackFile)) {
        Fail "$fallbackFile is missing"
        $allPassed = $false
    } else {
        $fallbackContent = Get-Content $fallbackFile -Raw
        $fallbackChecks = @(
            @{ label = "calendar fallback (SQLite, #232)"; pattern = "CalendarSQLiteFallback" },
            @{ label = "docs fallback (ODF, #233)";        pattern = "DocsODTFallback" },
            @{ label = "maps fallback (Nominatim, #234)";  pattern = "NominatimFallback" },
            @{ label = "tasks fallback (SQLite, #235)";    pattern = "TasksSQLiteFallback" },
            @{ label = "contacts fallback (SQLite, #236)"; pattern = "ContactsSQLiteFallback" }
        )
        foreach ($check in $fallbackChecks) {
            if ($fallbackContent -match $check.pattern) {
                Pass $check.label
            } else {
                Fail "$($check.label) -- pattern '$($check.pattern)' not found"
                $allPassed = $false
            }
        }
    }

    # -----------------------------------------------------------------------
    # Check 3: Google Maps API key
    # -----------------------------------------------------------------------
    Write-Host ""
    Write-Host "--- 3. GOOGLE_MAPS_API_KEY env var ---"
    if ($env:GOOGLE_MAPS_API_KEY -and $env:GOOGLE_MAPS_API_KEY.Length -gt 5) {
        Pass "GOOGLE_MAPS_API_KEY is set (length=$($env:GOOGLE_MAPS_API_KEY.Length))"
    } else {
        Fail "GOOGLE_MAPS_API_KEY is not set -- maps_geocode will fail. Set it before starting Cerebral."
        $allPassed = $false
    }

    # -----------------------------------------------------------------------
    # Check 4: Local docs directory (Docs fallback)
    # -----------------------------------------------------------------------
    Write-Host ""
    Write-Host "--- 4. Local docs directory for Docs fallback ---"
    $docsDir = if ($env:LOCAL_DOCS_DIR) { $env:LOCAL_DOCS_DIR } else { Join-Path $HOME "Documents\OpenMind" }
    if (Test-Path $docsDir) {
        Pass "Docs directory exists: $docsDir"
    } else {
        Write-Host "INFO: $docsDir does not exist yet -- Cerebral creates it on first use." -ForegroundColor Yellow
        Pass "Docs directory will be auto-created: $docsDir"
    }

    # -----------------------------------------------------------------------
    # Check 5: _GOOGLE_SCOPES in cerebral/main.py
    # -----------------------------------------------------------------------
    Write-Host ""
    Write-Host "--- 5. _GOOGLE_SCOPES in cerebral/main.py ---"
    $mainPy = "cerebral\main.py"
    if (-not (Test-Path $mainPy)) {
        Fail "$mainPy is missing"
        $allPassed = $false
    } else {
        $mainContent = Get-Content $mainPy -Raw
        $scopeChecks = @(
            @{ label = "documents scope (#224)";    pattern = "auth/documents" },
            @{ label = "spreadsheets scope (#225)"; pattern = "auth/spreadsheets" },
            @{ label = "tasks scope (#227)";        pattern = "auth/tasks" },
            @{ label = "drive scope (#228)";        pattern = 'auth/drive"' },
            @{ label = "contacts scope (#229)";     pattern = "auth/contacts" }
        )
        foreach ($check in $scopeChecks) {
            if ($mainContent -match $check.pattern) {
                Pass $check.label
            } else {
                Fail "$($check.label) -- scope not found in _GOOGLE_SCOPES"
                $allPassed = $false
            }
        }
    }

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    Write-Host ""
    if ($allPassed) {
        Write-Host "SUCCESS -- all pre-conditions pass. Proceed with docs/v1-live-verify.md." -ForegroundColor Green
    } else {
        Write-Host "FAILED -- one or more pre-conditions failed. Fix the issues above before running the human checklist." -ForegroundColor Red
    }

} catch {
    Write-Host ""
    Write-Host "FAILED -- unexpected error: $_" -ForegroundColor Red
    $allPassed = $false
} finally {
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}

if (-not $allPassed) { exit 1 }
