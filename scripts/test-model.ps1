# test-model.ps1 -- double-click to check whether Felix's active model is reachable.
# ASCII-only body + pause-on-exit per CLAUDE.md operator-script rules.

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "FAILED: python is not on PATH." -ForegroundColor Red
        exit 1
    }

    Write-Host "Testing the active model connection..." -ForegroundColor Cyan
    & python (Join-Path $PSScriptRoot "test_model.py")
    $code = $LASTEXITCODE

    Write-Host ""
    if ($code -eq 0) {
        Write-Host "SUCCESS: model is connected." -ForegroundColor Green
    } else {
        Write-Host "FAILED: model is NOT reachable (see the line above)." -ForegroundColor Red
    }
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
