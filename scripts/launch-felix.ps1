# launch-felix.ps1 -- one-click launcher for OpenMind / Felix.
#
# Starts Cerebral (Python backend) and the tray + Main window (Electron)
# as background processes, then exits. Both processes survive after the
# launcher returns. Quit Felix from the tray menu -- the tray sends
# {type: shutdown} to Cerebral over WebSocket on quit and both processes
# shut down cleanly.
#
# Safe to invoke from a hidden-console shortcut (see install-shortcut.ps1).
# When run interactively from PowerShell, prints a brief status line and
# self-closes; no Read-Host -- the user's interaction surface is the tray.

# -Restart: invoked by the tray's "Restart Felix" menu item (#439). The tray
# has just sent Cerebral the shutdown event and is quitting; instead of
# refusing to double-launch, wait for port 7766 to free up, then boot.
# -CerebralOnly: the relaunched tray passes this alongside -Restart -- it is
# already the running tray, so the launcher must boot Cerebral and nothing else.
param([switch]$Restart, [switch]$CerebralOnly)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

$CEREBRAL_PORT = 7766
# Cerebral cold-starts slowly on Win10: Kokoro TTS warmup + ChromaDB +
# 50+ plugin discovery is routinely 30-45s, sometimes more on first
# launch after reboot. Old 15s timeout was the silent-fail bug
# (2026-06-03).
$WAIT_SECONDS  = 120
$POLL_MS       = 500

# Always log to launcher.log so a hidden-console shortcut still leaves
# a post-mortem. Append rather than overwrite -- repeated launches are
# rare and the time history is useful when something starts misbehaving.
$LAUNCHER_LOG = Join-Path $repoRoot "launcher.log"
function Log($msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -LiteralPath $LAUNCHER_LOG -Value $line -ErrorAction SilentlyContinue
}
Log "=== launcher started ==="
Log "repoRoot=$repoRoot"

function Test-CerebralPort {
    [bool](Get-NetTCPConnection -LocalPort $CEREBRAL_PORT -State Listen `
        -ErrorAction SilentlyContinue)
}

# 2026-09-16 -- companion to Remove-OrphanedCerebral below, added after that
# function caused a real outage the same day it landed. Test-CerebralPort
# only answers "is ANYONE listening," which is exactly the gap that made
# the reap dangerous: when the tray's reconnect-watchdog fires a second
# launcher invocation, the LOSER's own spawn can crash on OSError (the
# port the winner already bound) while its wait loop still sees the
# WINNER's process satisfying Test-CerebralPort -- so the loser declares
# itself "ready" and then reaps everyone except its own (already-dead)
# PID, killing the actual winner. Get-CerebralListenerPid answers "WHO is
# listening," so the wait loop and the reap can both check identity, not
# just presence.
function Get-CerebralListenerPid {
    $conn = Get-NetTCPConnection -LocalPort $CEREBRAL_PORT -State Listen `
        -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($conn) { $conn.OwningProcess } else { $null }
}

# #521 -- "port listening" is not "Felix running": the tray can die while
# Cerebral keeps heartbeating (and vice versa). Match electron.exe main
# processes launched from THIS repo's tray dir.
function Test-TrayRunning {
    $trayPattern = "*" + (Join-Path $repoRoot "tray") + "*"
    [bool](Get-CimInstance Win32_Process -Filter "Name='electron.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like $trayPattern })
}

# 2026-09-16 -- found live: the -Restart port-free wait above is not proof
# the OLD Cerebral actually exited, only that it stopped listening. Its
# shutdown path can close the WebSocket listener before the process itself
# finishes tearing down, so "port free" fires while the old PID keeps
# running (observed: it survived as an orphan for 30+ minutes, still
# ticking its own paper-trade scheduler loop in parallel with the new one --
# two live Cerebrals is worse than the double-launch this script otherwise
# guards against). A launcher racing itself compounds this: launcher.log
# showed two "-Restart" invocations firing ~12s apart for one restart
# request, each independently seeing the port free and spawning its own
# cerebral.main, so relying on the pre-spawn port check alone cannot be
# made race-proof against a second, uninvited invocation of this same
# script.
#
# Fix applied once we DO have unambiguous information: right after THIS
# invocation confirms its own spawn is the one actually listening on
# :$CEREBRAL_PORT, reap every OTHER python.exe process running
# cerebral.main by PID (not by port state) -- whether it's an old instance
# that failed to fully exit, or the loser of a racing double-invocation
# that crashed on bind but didn't fully unwind. $keepId is the PID this
# invocation itself spawned (from Start-Process -PassThru), so the winner
# is never at risk of killing itself.
function Remove-OrphanedCerebral($keepId) {
    $stray = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'cerebral\.main' -and $_.ProcessId -ne $keepId }
    foreach ($p in $stray) {
        Log ("Reaping orphaned Cerebral: pid={0} (not the process this launch spawned)" -f $p.ProcessId)
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

# ---- 0. precheck: prerequisites + first-run install state ---------------------
#
# Catches the common "I cloned the repo and double-clicked Felix" failure
# modes before we spawn anything. Hard-fails (and tells the user the exact
# command to run) when the prereq is load-bearing; soft-warns when Cerebral
# can degrade around it (e.g. missing Vosk model -> voice disabled, typing
# still works).

function Test-Prerequisites {
    $hardFail = $false

    # python on PATH
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Log "MISSING: 'python' is not on PATH."
        Log "  Install Python 3.10+ from https://www.python.org/ and re-open this shortcut."
        $hardFail = $true
    } else {
        # cerebral package importable -- catches missing `pip install -e .`.
        # Using the call operator (not Start-Process -ArgumentList) because
        # the latter strips embedded quoting; `python -c "import cerebral"`
        # arrives at Python as `python -c import cerebral` and errors out.
        & python -c "import cerebral" *> $null
        if ($LASTEXITCODE -ne 0) {
            Log "MISSING: 'cerebral' package not importable."
            Log "  Run from the repo root: pip install -e ."
            $hardFail = $true
        }
    }

    # npm on PATH
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Log "MISSING: 'npm' is not on PATH."
        Log "  Install Node.js 22.14+ from https://nodejs.org/ and re-open this shortcut."
        $hardFail = $true
    }

    # tray dependencies installed
    $trayModules = Join-Path $repoRoot "tray\node_modules"
    if (-not (Test-Path $trayModules)) {
        Log "MISSING: tray dependencies are not installed."
        Log "  Run from the repo root: cd tray; npm install"
        $hardFail = $true
    }

    # Vosk model present (soft -- Cerebral degrades to typing-only)
    $voskDir = Join-Path $repoRoot "cerebral\models\vosk-model-small-en-us-0.15"
    if (-not (Test-Path $voskDir)) {
        Log "NOTE: Vosk speech model not found -- voice disabled (text input still works)."
    }

    if ($hardFail) {
        Log "FAILED: fix the items above, then re-launch Felix."
        exit 1
    }
}

Test-Prerequisites

# ---- 1. refuse to double-launch (or, on -Restart, wait for the old one) ------
if ($Restart) {
    Log "Restart requested -- waiting for the old Cerebral to release :$CEREBRAL_PORT..."
    $freeDeadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $freeDeadline -and (Test-CerebralPort)) {
        Start-Sleep -Milliseconds 500
    }
    if (Test-CerebralPort) {
        Log "Port $CEREBRAL_PORT still in use after 30s -- old Cerebral did not shut down. Aborting."
        exit 1
    }
    Log "Port free -- proceeding with relaunch."
} elseif (Test-CerebralPort) {
    if (Test-TrayRunning) {
        # #441 -- relaunching while running used to be a silent no-op; the user
        # cannot tell a hidden window from a dead app. Surface the window instead.
        Log "Felix is already running -- surfacing the main window."
        & python (Join-Path $PSScriptRoot "open-felix.py") 2>$null
        exit 0
    }
    # #521 -- Cerebral alive, tray dead: boot the tray only.
    Log "Cerebral is running but the tray is not -- starting the tray only."
    $SkipCerebral = $true
}

# ---- 2. start Cerebral --------------------------------------------------------
# Hidden window + redirected output: no stray console on double-click. Cerebral
# stdout/stderr land in cerebral.log / cerebral.err.log (reachable from the
# tray "Show Logs" menu) instead of a visible window.
# #521: sections 2+3 are skipped entirely when Cerebral is already up and only
# the tray needs booting. Body deliberately not re-indented (minimal diff).
if (-not $SkipCerebral) {
Log "Starting Cerebral (Python backend)..."
$cerebralLog = Join-Path $repoRoot "cerebral.log"

# Custom-endpoint HTTP timeout. Measured 2026-08-17: the budd endpoint
# generates at ~2.5 tokens/sec (708 tok in 297.7s, 638 tok in 252.9s, on
# uncached prompts). Input is nearly free -- 64k tokens of prompt returned in
# 47.5s -- so the binding constraint is OUTPUT length. router.py's 300s default
# therefore caps a reply at roughly 750 tokens, and a self_dev edit emitting
# SEARCH/REPLACE blocks routinely needs more than that, which is why those runs
# died with ReadTimeout while a 5-token health probe always looked fine.
# 1200s allows ~3000 tokens of output. It is a CEILING, not a delay: a fast
# reply still returns immediately.
if (-not $env:CLAW_TIMEOUT_S) { $env:CLAW_TIMEOUT_S = "1200" }
Log ("  CLAW_TIMEOUT_S = {0}s" -f $env:CLAW_TIMEOUT_S)
$cerebralErr = ($cerebralLog -replace '\.log$', '.err.log')

# Rotate existing logs before overwriting (S2: ADR-0032 / FELIX-AUDIT S2)
function Rotate-Log($logPath) {
    if (Test-Path $logPath -ErrorAction SilentlyContinue) {
        $size = (Get-Item $logPath).Length
        if ($size -gt 0) {
            $timestamp = Get-Date -Format "yyyyMMddTHHmmss"
            # No regex backreference here on purpose. "_${timestamp}$$1" looks
            # like it appends $1, but PowerShell expands $$ inside a
            # double-quoted string before -replace ever sees it, so the result
            # was 'cerebral_20260917T223909' + a literal '1' -- the .log
            # extension silently destroyed. That also made the retention sweep
            # below (-Filter "cerebral_*.log") match nothing, so rotated logs
            # would have accumulated forever. Substituting the literal
            # extension needs no backreference at all.
            $leaf = Split-Path $logPath -Leaf
            $dest = $leaf -replace '\.log$', "_$timestamp.log"
            Rename-Item -LiteralPath $logPath -NewName $dest -Force -ErrorAction SilentlyContinue
        }
    }
}
Rotate-Log $cerebralLog
Rotate-Log $cerebralErr

# Keep only the 5 newest rotated logs per kind
$logsDir = Split-Path $cerebralLog
Get-ChildItem -Path $logsDir -Filter "cerebral_*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 5 |
    Remove-Item -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $logsDir -Filter "cerebral.err_*.log" -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 5 |
    Remove-Item -Force -ErrorAction SilentlyContinue

$cerebral = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m","cerebral.main" `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $cerebralLog `
    -RedirectStandardError $cerebralErr `
    -PassThru

# ---- 3. wait for Cerebral to bind ws://localhost:7766 -------------------------
Log "Waiting for Cerebral to bind :$CEREBRAL_PORT (up to ${WAIT_SECONDS}s)..."
$deadline = (Get-Date).AddSeconds($WAIT_SECONDS)
$ready = $false
$stoodDown = $false
$lastTick = 0
while ((Get-Date) -lt $deadline) {
    $listenerPid = Get-CerebralListenerPid
    if ($listenerPid -eq $cerebral.Id) { $ready = $true; break }
    if ($listenerPid) {
        # Someone else already bound the port -- not this invocation's own
        # spawn. Almost certainly a sibling launcher invocation (the tray's
        # reconnect-watchdog can fire this script twice for one restart,
        # observed live) that won the race. That other process is the real
        # Cerebral, not an orphan -- stand down instead of reaping it.
        Log "Another Cerebral (pid=$listenerPid) already bound :$CEREBRAL_PORT -- standing down (this looks like a duplicate launcher invocation)."
        if (-not $cerebral.HasExited) {
            Log "Terminating this invocation's own redundant spawn (pid=$($cerebral.Id))."
            Stop-Process -Id $cerebral.Id -Force -ErrorAction SilentlyContinue
        }
        $stoodDown = $true
        break
    }
    if ($cerebral.HasExited) {
        Log "Cerebral exited (code=$($cerebral.ExitCode)) before binding -- check the Python console."
        exit 1
    }
    # Heartbeat to launcher.log every 10s so a hung-poll is observable
    $elapsed = [int]((Get-Date) - $deadline.AddSeconds(-$WAIT_SECONDS)).TotalSeconds
    if ($elapsed - $lastTick -ge 10) { Log "  still waiting (${elapsed}s)..."; $lastTick = $elapsed }
    Start-Sleep -Milliseconds $POLL_MS
}

if ($stoodDown) {
    Log "=== launcher done (stood down for another instance) ==="
    exit 0
}
if (-not $ready) {
    Log "Cerebral did not bind :$CEREBRAL_PORT within ${WAIT_SECONDS}s -- aborting tray launch."
    Log "Check cerebral.log / cerebral.err.log for errors (tray menu -> Show Logs)."
    exit 1
}
Log "Cerebral is listening on :$CEREBRAL_PORT."
Remove-OrphanedCerebral $cerebral.Id

if ($CerebralOnly) {
    Log "CerebralOnly: tray already running -- skipping tray launch."
    Log "=== launcher done ==="
    exit 0
}
} # end if (-not $SkipCerebral) -- #521

# #521 -- dedupe guard: a manual launch while the tray survives (e.g. Cerebral
# died alone) must not boot a second tray.
if (Test-TrayRunning) {
    Log "Tray is already running -- skipping tray launch."
    Log "=== launcher done ==="
    exit 0
}

# ---- 4. start the tray (Electron) --------------------------------------------
# Call electron.exe directly. No npm.cmd, no electron.cmd, no PowerShell
# wrapper, no Start-Process WindowStyle dance -- every one of those layers
# died silently on the user's Win10 box (see .learnings/LEARNINGS.md
# 2026-06-03). electron.exe is a normal GUI process; Start-Process spawns
# it cleanly and it survives without a console.
#
# The argument is the app directory; Electron reads main.js per
# tray/package.json's "main" field.
Log "Starting Felix tray + Main window..."
$trayDir     = Join-Path $repoRoot "tray"
$electronExe = Join-Path $trayDir "node_modules\electron\dist\electron.exe"
Log "trayDir=$trayDir"
Log "electronExe=$electronExe"

if (-not (Test-Path $electronExe)) {
    Log "MISSING: $electronExe not found -- run 'cd tray; npm install' from the repo root."
    exit 1
}

try {
    $tray = Start-Process `
        -FilePath $electronExe `
        -ArgumentList $trayDir `
        -WorkingDirectory $trayDir `
        -PassThru `
        -ErrorAction Stop
    Log "Start-Process electron returned PID=$($tray.Id)"
} catch {
    Log "Start-Process electron THREW: $_"
    exit 1
}

Start-Sleep -Seconds 3
try { $tray.Refresh() } catch { }
if ($tray.HasExited) {
    Log "ERROR: electron exited with code $($tray.ExitCode) within 3s."
} else {
    Log "Tray running (electron PID $($tray.Id))."
}

Log "Felix is up. Quit from the tray to shut down."
Log "=== launcher done ==="
Start-Sleep -Seconds 1
