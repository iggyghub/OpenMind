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

# #521 -- "port listening" is not "Felix running": the tray can die while
# Cerebral keeps heartbeating (and vice versa). Match electron.exe main
# processes launched from THIS repo's tray dir.
function Test-TrayRunning {
    $trayPattern = "*" + (Join-Path $repoRoot "tray") + "*"
    [bool](Get-CimInstance Win32_Process -Filter "Name='electron.exe'" `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like $trayPattern })
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
$cerebral = Start-Process `
    -FilePath "python" `
    -ArgumentList "-m","cerebral.main" `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $cerebralLog `
    -RedirectStandardError  ($cerebralLog -replace '\.log$', '.err.log') `
    -PassThru

# ---- 3. wait for Cerebral to bind ws://localhost:7766 -------------------------
Log "Waiting for Cerebral to bind :$CEREBRAL_PORT (up to ${WAIT_SECONDS}s)..."
$deadline = (Get-Date).AddSeconds($WAIT_SECONDS)
$ready = $false
$lastTick = 0
while ((Get-Date) -lt $deadline) {
    if (Test-CerebralPort) { $ready = $true; break }
    if ($cerebral.HasExited) {
        Log "Cerebral exited (code=$($cerebral.ExitCode)) before binding -- check the Python console."
        exit 1
    }
    # Heartbeat to launcher.log every 10s so a hung-poll is observable
    $elapsed = [int]((Get-Date) - $deadline.AddSeconds(-$WAIT_SECONDS)).TotalSeconds
    if ($elapsed - $lastTick -ge 10) { Log "  still waiting (${elapsed}s)..."; $lastTick = $elapsed }
    Start-Sleep -Milliseconds $POLL_MS
}

if (-not $ready) {
    Log "Cerebral did not bind :$CEREBRAL_PORT within ${WAIT_SECONDS}s -- aborting tray launch."
    Log "Check cerebral.log / cerebral.err.log for errors (tray menu -> Show Logs)."
    exit 1
}
Log "Cerebral is listening on :$CEREBRAL_PORT."

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
