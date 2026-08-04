# set-felix-session-login.ps1 -- store the Felix isolated-session Windows login.
#
# Double-click this to enter the password for the dedicated standard Windows
# user "Felix" (the second-session account, ADR-0016 #601/#604). It is written
# to Windows Credential Manager under service "openmind-felix-session" so the
# #604 auto-provisioning can read it back. The password is prompted by the
# Python helper (never echoed, never in argv/history) and never printed.
#
# Nothing consumes the credential yet (#604 provisioning is unbuilt) -- storing
# early is safe: your own password in your own vault.

param([string]$User = "Felix")

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $py) {
        Write-Host "FAILED: python not found on PATH." -ForegroundColor Red
        exit 1
    }

    Write-Host "Felix session login -- storing to Windows Credential Manager" -ForegroundColor Cyan
    Write-Host ""

    & $py.Source (Join-Path $repoRoot "scripts\set_felix_session_login.py") --user $User
    $code = $LASTEXITCODE

    Write-Host ""
    if ($code -eq 0) {
        Write-Host "SUCCESS: Felix session login stored." -ForegroundColor Green
    } else {
        Write-Host "FAILED: nothing stored (exit $code). See the message above." -ForegroundColor Red
    }
    exit $code
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
