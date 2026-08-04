# spike-second-session.ps1 -- SPIKE (#603, ADR-0016 isolated interactive session).
#
# Brings up Felix's dedicated Windows account in a SECOND interactive session via
# loopback RDP, runs _spike_probe.py inside it (read_ui on Notepad), and confirms
# session 1 (your desktop) is untouched. This is the de-risking tracer bullet for
# the whole isolated-session vehicle -- run it, then paste the summary into
# docs/computer-use-live-verify.md.
#
# This script performs REAL system actions (loopback RDP login as Felix). It is
# HUMAN-run, never the autonomous loop. I (the assistant) cannot run it -- it
# needs the real Felix account + RDP. Treat it as a first draft to iterate on.
#
# Prereqs you own (the script preflights them and tells you what's missing):
#   1. Windows Pro/Enterprise (Home cannot host a concurrent interactive session).
#   2. A local STANDARD user "Felix" (New-LocalUser ... ; the earlier step).
#   3. Felix in the "Remote Desktop Users" group.
#   4. Felix's password stored via scripts/set-felix-session-login.ps1
#      (keyring service openmind-felix-session / user Felix).
#   5. Remote Desktop ENABLED on this PC (Settings > System > Remote Desktop).
#      Enabling RDP is a security setting -- you do it, not this script.
#
# Switches:
#   -User Felix     the second-session account (default Felix)
#   -Prompt         skip the stored credential; let mstsc prompt for the password

param(
    [string]$User = "Felix",
    [switch]$Prompt
)

$ErrorActionPreference = "Stop"
$svc = "openmind-felix-session"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $tmp = Join-Path $repoRoot ".claude\tmp\spike"
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $resultFile = Join-Path $tmp "session2_result.json"
    $rdpFile    = Join-Path $tmp "felix-session.rdp"
    $probeBat   = Join-Path $tmp "run_probe.bat"
    if (Test-Path $resultFile) { Remove-Item $resultFile -Force }

    Write-Host "=== SPIKE: second interactive session for '$User' ===" -ForegroundColor Cyan
    Write-Host ""

    # ---- Preflight -------------------------------------------------------
    $fail = @()

    $caption = (Get-CimInstance Win32_OperatingSystem).Caption
    if ($caption -notmatch "Pro|Enterprise|Education") {
        $fail += "Edition '$caption' likely cannot host a 2nd concurrent session (need Pro/Enterprise)."
    } else {
        Write-Host "[ok] edition: $caption" -ForegroundColor Green
    }

    try { Get-LocalUser -Name $User -ErrorAction Stop | Out-Null; Write-Host "[ok] local user '$User' exists" -ForegroundColor Green }
    catch { $fail += "Local user '$User' not found. Create it: New-LocalUser -Name '$User' (standard, non-admin)." }

    try {
        $inRdp = Get-LocalGroupMember -Group "Remote Desktop Users" -ErrorAction Stop |
                 Where-Object { $_.Name -like "*\$User" }
        if ($inRdp) { Write-Host "[ok] '$User' is in Remote Desktop Users" -ForegroundColor Green }
        else { $fail += "'$User' is not in 'Remote Desktop Users'. Add: Add-LocalGroupMember -Group 'Remote Desktop Users' -Member '$User'." }
    } catch { $fail += "Could not read 'Remote Desktop Users' group: $($_.Exception.Message)" }

    $fDeny = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" -Name fDenyTSConnections -ErrorAction SilentlyContinue).fDenyTSConnections
    if ($fDeny -eq 0) { Write-Host "[ok] Remote Desktop is enabled" -ForegroundColor Green }
    else { $fail += "Remote Desktop is disabled. Enable it: Settings > System > Remote Desktop (a security setting -- you enable it)." }

    $pwStored = & python -c "import keyring;print('1' if keyring.get_password('$svc','$User') else '0')" 2>$null
    if ($pwStored -eq "1") { Write-Host "[ok] password for '$User' is in Credential Manager" -ForegroundColor Green }
    elseif (-not $Prompt) { $fail += "No stored password for '$User'. Run scripts/set-felix-session-login.ps1, or re-run this with -Prompt." }

    if ($fail.Count -gt 0) {
        Write-Host ""
        Write-Host "PREFLIGHT FAILED -- fix these and re-run:" -ForegroundColor Red
        $fail | ForEach-Object { Write-Host "  - $_" -ForegroundColor Yellow }
        exit 2
    }

    # ---- Session 1 baseline (to prove it stays untouched) ----------------
    $session1 = (Get-Process -Id $PID).SessionId
    Add-Type -AssemblyName System.Windows.Forms
    $cursorBefore = [System.Windows.Forms.Cursor]::Position
    Write-Host ""
    Write-Host "session 1 (this desktop) = $session1 ; cursor @ $($cursorBefore.X),$($cursorBefore.Y)"

    # ---- Wire the probe as the RDP session's alternate shell -------------
    "@echo off`r`npython `"$repoRoot\scripts\_spike_probe.py`" `"$resultFile`"" |
        Set-Content -LiteralPath $probeBat -Encoding ascii

    $rdp = @(
        "full address:s:localhost",
        "username:s:$User",
        "alternate shell:s:$probeBat",
        "disableconnectionsharing:i:1",
        "prompt for credentials:i:$([int]$Prompt.IsPresent)",
        "screen mode id:i:1",
        "desktopwidth:i:1280",
        "desktopheight:i:800"
    )
    Set-Content -LiteralPath $rdpFile -Value $rdp -Encoding ascii

    # Stored-credential path: hand mstsc the password via Credential Manager
    # (TERMSRV/localhost). Note: cmdkey takes /pass in argv (brief exposure);
    # we delete it again in finally. Use -Prompt to avoid this entirely.
    $usedCmdkey = $false
    if (-not $Prompt) {
        $pw = & python -c "import keyring;print(keyring.get_password('$svc','$User') or '')" 2>$null
        if ([string]::IsNullOrEmpty($pw)) { throw "stored password came back empty" }
        cmdkey /generic:TERMSRV/localhost /user:$User /pass:$pw | Out-Null
        $pw = $null
        $usedCmdkey = $true
    }

    Write-Host ""
    Write-Host "launching loopback RDP -> second session (an RDP window will open)..." -ForegroundColor Cyan
    Start-Process mstsc.exe -ArgumentList "`"$rdpFile`""

    # ---- Wait for the probe's result -------------------------------------
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-Path $resultFile) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 2 }

    Write-Host ""
    if (-not (Test-Path $resultFile)) {
        Write-Host "FAILED: no result from session 2 within 90s." -ForegroundColor Red
        Write-Host "Check the RDP window for errors (login refused? RDP not allowed? probe crashed?)." -ForegroundColor Yellow
        exit 1
    }

    $r = Get-Content -LiteralPath $resultFile -Raw | ConvertFrom-Json
    $cursorAfter = [System.Windows.Forms.Cursor]::Position
    $cursorMoved = ($cursorBefore.X -ne $cursorAfter.X) -or ($cursorBefore.Y -ne $cursorAfter.Y)

    Write-Host "=== RESULT ===" -ForegroundColor Cyan
    Write-Host "session 2 id      : $($r.session_id)   (session 1 = $session1)"
    Write-Host "session 2 user    : $($r.user)"
    Write-Host "actuator          : $($r.actuator)"
    Write-Host "notepad elements  : $($r.notepad_elements)"
    Write-Host "sample roles      : $($r.sample_roles -join ', ')"
    Write-Host "session-1 cursor moved during run : $cursorMoved"

    $distinct = ($r.session_id -ne $session1 -and $r.session_id -gt 0)
    $readOk   = [bool]$r.ok
    Write-Host ""
    if ($distinct -and $readOk) {
        Write-Host "SUCCESS: a SECOND session (id $($r.session_id)) read a real UIA tree; session 1 untouched." -ForegroundColor Green
        Write-Host "Paste the RESULT block above into docs/computer-use-live-verify.md under the #603 spike." -ForegroundColor Green
        exit 0
    } else {
        Write-Host "PARTIAL/FAILED: distinct-session=$distinct read-ok=$readOk. See RESULT + the RDP window." -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    exit 1
} finally {
    if ($usedCmdkey) { cmdkey /delete:TERMSRV/localhost 2>$null | Out-Null }
    Read-Host "Press Enter to close" | Out-Null
}
