# run-felix-audit.ps1 -- drive the FELIX-AUDIT.md self_dev campaign.
#
# Unlike run-cross-stock-followups.ps1 and friends, this does NOT spawn Claude
# Code sessions. FELIX-AUDIT is a Felix-built campaign (ADR-0015): the work
# happens inside Cerebral via the self_dev_campaign tool, which clones, edits,
# tests, opens the PR, auto-merges and rewrites the driver itself. This script
# is the operator surface around that -- guard rails, driver parsing, and a
# readable outcome.
#
# Default MaxSlices is 1 on purpose. FELIX-AUDIT.md's run mode says: run S1,
# stop, confirm the PR actually merged and the driver advanced, then release
# the rest. Pass -MaxSlices N deliberately once that checkpoint has passed.
#
# ASCII-only by repo convention: Windows PowerShell 5.1 reads .ps1 files in the
# ANSI codepage when there is no BOM, so a non-ASCII character in a string
# literal corrupts the parser.

param(
    [int]$MaxSlices = 1,
    [switch]$NoPause
)

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    $driver   = Join-Path $repoRoot "FELIX-AUDIT.md"
    $helper   = Join-Path $PSScriptRoot "felix_audit_call.py"

    function Log($msg) {
        Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg)
    }

    # Tolerate markdown on the field line: "## Status: done", "- **Model:** sonnet".
    # Anchor after any leading #/-/*/space and allow ** around the colon. Queue
    # lines ("- [x] ... ") start with "[" after the dash, so the ^[\s#\-\*]*
    # anchor never reaches their inline text -- no false match. This is the
    # repaired pattern from run-cross-stock-followups.ps1; older runners had a
    # dead regex here and never auto-stopped on done/blocked.
    function Get-DriverField($name, $default) {
        $m = Select-String -LiteralPath $driver `
            -Pattern ("^[\s#\-\*]*{0}\s*:\s*\**\s*(\S+)" -f $name) | Select-Object -First 1
        if ($null -eq $m) { return $default }
        return $m.Matches[0].Groups[1].Value.ToLower()
    }

    function Get-DriverActiveLine {
        $m = Select-String -LiteralPath $driver `
            -Pattern "^[\s#\-\*]*\**Active\**\s*:\s*(.+)$" | Select-Object -First 1
        if ($null -eq $m) { return "" }
        return $m.Matches[0].Groups[1].Value.Trim()
    }

    if (-not (Test-Path $driver)) {
        Write-Host "FAILED: FELIX-AUDIT.md not found at $driver" -ForegroundColor Red
        exit 1
    }
    if (-not (Test-Path $helper)) {
        Write-Host "FAILED: helper not found at $helper" -ForegroundColor Red
        exit 1
    }

    $status = Get-DriverField "Status" "ready"
    $active = Get-DriverActiveLine

    Log ("driver : {0}" -f $driver)
    Log ("status : {0}" -f $status)
    Log ("active : {0}" -f $active)

    if ($status -eq "done") {
        Write-Host ""
        Write-Host "Nothing to do: FELIX-AUDIT.md says Status: done." -ForegroundColor Green
        Write-Host "SUCCESS" -ForegroundColor Green
        exit 0
    }
    if ($status -eq "blocked") {
        Write-Host ""
        Write-Host "Refusing to start: FELIX-AUDIT.md says Status: blocked." -ForegroundColor Yellow
        Write-Host "A slice failed its tests and needs you. Read the Status line for the reason," -ForegroundColor Yellow
        Write-Host "fix it, then reset the line to 'Status: ready' by hand -- self_dev_campaign" -ForegroundColor Yellow
        Write-Host "never clears 'blocked' on its own, so a stale one silently no-ops forever." -ForegroundColor Yellow
        Write-Host "FAILED" -ForegroundColor Red
        exit 1
    }

    # The campaign runs inside Cerebral; if it is not up there is nothing to
    # drive. Checking here gives a clear message instead of a websocket stack.
    $listening = @(Get-NetTCPConnection -LocalPort 7766 -State Listen -ErrorAction SilentlyContinue)
    if ($listening.Count -eq 0) {
        Write-Host ""
        Write-Host "FAILED: nothing is listening on port 7766 -- Cerebral is not running." -ForegroundColor Red
        Write-Host "Start Felix first:  .\scripts\launch-felix.ps1" -ForegroundColor Red
        Write-Host "(invoke it directly, NOT via Start-Process -- that silently no-ops here)" -ForegroundColor Red
        exit 1
    }

    # Pre-flight 2 guard. S0 (#1281/#1282) is what widens self_dev's reach from
    # 38% of cerebral/main.py to 95%. Cerebral loads main.py at boot, so a merged
    # -but-not-restarted S0 is the worst case: the campaign runs, every main.py
    # slice silently sees a third of the file, and nothing reports an error.
    # Catch both halves -- code not merged, and code merged but not loaded.
    $mainPy = Join-Path $repoRoot "cerebral\main.py"
    $stillFractioned = Select-String -LiteralPath $mainPy `
        -Pattern "^_SELF_DEV_PER_FILE_FRACTION\s*=" | Select-Object -First 1
    if ($null -ne $stillFractioned) {
        Write-Host ""
        Write-Host "FAILED: S0 is not in the working tree yet." -ForegroundColor Red
        Write-Host "cerebral/main.py still defines _SELF_DEV_PER_FILE_FRACTION, so self_dev" -ForegroundColor Red
        Write-Host "can only see ~38% of it. Merge PR #1282, pull master, then restart Felix." -ForegroundColor Red
        exit 1
    }

    $cerebralProc = Get-Process -Id ($listening[0].OwningProcess) -ErrorAction SilentlyContinue
    if ($null -ne $cerebralProc) {
        $mainPyWritten = (Get-Item $mainPy).LastWriteTime
        if ($mainPyWritten -gt $cerebralProc.StartTime) {
            Write-Host ""
            Write-Host "FAILED: the running Cerebral predates the current cerebral/main.py." -ForegroundColor Red
            Write-Host ("  main.py written : {0}" -f $mainPyWritten) -ForegroundColor Red
            Write-Host ("  Cerebral started: {0}" -f $cerebralProc.StartTime) -ForegroundColor Red
            Write-Host "It is running the OLD edit-budget code. Restart Felix before starting" -ForegroundColor Red
            Write-Host "the campaign, or every main.py slice silently sees a third of the file." -ForegroundColor Red
            exit 1
        }
    }

    if ($MaxSlices -gt 1) {
        Log ("MaxSlices={0} -- running unattended past the S1 checkpoint." -f $MaxSlices)
        Log "Reminder: S6/S7/S8 must not start until S5 is MERGED, not merely committed."
    }

    Log ("calling self_dev_campaign (max_slices={0})" -f $MaxSlices)
    Write-Host ""

    & python $helper $driver $MaxSlices
    $rc = $LASTEXITCODE

    Write-Host ""
    $statusAfter = Get-DriverField "Status" "ready"
    $activeAfter = Get-DriverActiveLine
    Log ("status now : {0}" -f $statusAfter)
    Log ("active now : {0}" -f $activeAfter)

    if ($rc -ne 0) {
        Write-Host ""
        Write-Host "FAILED: could not reach Felix to start the campaign." -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    if ($statusAfter -eq "blocked") {
        Write-Host "Campaign stopped: a slice set Status: blocked. Read FELIX-AUDIT.md." -ForegroundColor Yellow
        Write-Host "FAILED" -ForegroundColor Red
        exit 1
    }

    if ($activeAfter -eq $active -and $statusAfter -ne "done") {
        Write-Host "WARNING: the Active line did not move." -ForegroundColor Yellow
        Write-Host "The campaign returned without landing a slice. Check the result JSON above" -ForegroundColor Yellow
        Write-Host "and cerebral.err.log before re-running." -ForegroundColor Yellow
        Write-Host "FAILED" -ForegroundColor Red
        exit 1
    }

    Write-Host "Verify before continuing -- 'succeeded' means exit 0, not merged:" -ForegroundColor Cyan
    Write-Host "  gh pr list --state merged --limit 5" -ForegroundColor Cyan
    Write-Host "SUCCESS" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ""
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
finally {
    if (-not $NoPause) { Read-Host "Press Enter to close" | Out-Null }
}
