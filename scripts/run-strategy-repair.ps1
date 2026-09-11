# run-strategy-repair.ps1 -- autonomous slice loop for the Strategy Repair campaign.
#
# Repeats: read STRATEGY-REPAIR.md 'Next slice' block -> launch a fresh headless Claude
# Code session (claude -p) on the recommended model -> session implements the active
# slice against tests/fixtures, runs the suite, opens a per-issue PR, MERGES it to
# master (so the next slice branches off a current master), rewrites the kickoff
# block -> loop.
#
# Each attempt is a brand-new session (keeps per-slice token count low);
# STRATEGY-REPAIR.md is the only memory between them. Logs land in
# .claude\tmp\strategy-repair-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-strategy-repair.ps1 (graceful, between steps)
#   - STRATEGY-REPAIR.md Status: done     (SR4 landed)
#   - STRATEGY-REPAIR.md Status: blocked  (a session decided it needs a human)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: this campaign touches only the generation/backtest-time path (_run_gauntlet,
# to_strategy, evaluate_signals). It must never touch live_tick.py's live dispatch
# path, risk_limits.py, or any broker/order code. The repair retry itself is bounded
# to exactly one extra to_strategy call per strategy -- never a loop.
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a terminal,
# type /login, complete the browser flow.

param(
    [int]$MaxSlices = 4,
    [int]$MaxAttempts = 3,
    [bool]$AutoResume = $true,
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "64000"

    $driver = Join-Path $repoRoot "STRATEGY-REPAIR.md"
    if (-not (Test-Path $driver)) { throw "STRATEGY-REPAIR.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\strategy-repair-loop"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $stopFile = Join-Path $stateDir "STOP"
    if (Test-Path $stopFile) { Remove-Item $stopFile -Force }

    $runStamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $loopLog  = Join-Path $stateDir ("loop-{0}.log" -f $runStamp)

    function Log($msg) {
        $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
        Write-Host $line
        Add-Content -LiteralPath $loopLog -Value $line
    }

    $shell = New-Object -ComObject WScript.Shell
    function Notify($title, $msg) {
        Log ("NOTIFY: {0} - {1}" -f $title, $msg)
        $shell.Popup($msg, 0, $title, 48) | Out-Null
    }
    function NotifyTimed($title, $msg, $seconds) {
        Log ("NOTIFY: {0} - {1}" -f $title, $msg)
        $shell.Popup($msg, $seconds, $title, 64) | Out-Null
    }

    # Parse "...resets 7:20pm..." into seconds-from-now until that clock time.
    function Get-SecondsUntilReset($text) {
        $buffer = 120
        $capSec = 28800   # 8h ceiling: enough to cover an overnight reset in one wait
        $default = 18900
        if ($text -match 'resets?\s+(\d{1,2}(:\d{2})?\s*[ap]\.?m\.?)') {
            $clock = $matches[1] -replace '\.', ''
            try {
                $target = [DateTime]::Parse($clock)
                $now = Get-Date
                if ($target -le $now) { $target = $target.AddDays(1) }
                $sec = [int]($target - $now).TotalSeconds + $buffer
                if ($sec -lt 300) { return 300 }
                if ($sec -gt $capSec) { return $capSec }
                return $sec
            } catch { return $default }
        }
        return $default
    }

    function Wait-WithStopCheck($seconds, $stopFile) {
        $elapsed = 0
        while ($elapsed -lt $seconds) {
            if (Test-Path $stopFile) { return $false }
            $chunk = [Math]::Min(60, $seconds - $elapsed)
            Start-Sleep -Seconds $chunk
            $elapsed += $chunk
        }
        return $true
    }

    function Get-DriverField($name, $default) {
        $m = Select-String -LiteralPath $driver `
            -Pattern ("^{0}:\s*(\S+)" -f $name) | Select-Object -First 1
        if ($null -eq $m) { return $default }
        return $m.Matches[0].Groups[1].Value.ToLower()
    }

    $claudeCmd = Get-Command claude.cmd -ErrorAction SilentlyContinue
    if ($null -eq $claudeCmd) {
        Notify "Strategy Repair loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in|Failed to authenticate"

    # Each slice MUST land on master before the next starts: SR2/SR3 both touch
    # to_strategy's call sites and would collide on a stale base otherwise.
    $rules = "Hard rules, in order: " +
             "(1) Read STRATEGY-REPAIR.md first. The active slice is named in the 'Next slice' block as 'SRx -- #N'. Run gh issue view N for its full near-diff-level spec -- follow it exactly, do not improvise scope. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b strategy-repair/srN-short-name origin/master). " +
             "(3) Implement ONLY that one slice exactly as the issue specifies. One PR per issue. " +
             "(4) SAFETY (highest priority): this campaign touches only cerebral/trading/sandboxed_eval.py, cerebral/trading_ideas.py, plugins/scheduler.py's _run_gauntlet, and cerebral/trading/books.py (SR4 only). Do NOT touch live_tick.py, risk_limits.py, or any broker/order code. The repair retry is bounded to exactly ONE extra to_strategy call per strategy -- never turn it into a loop. evaluate_signals' existing return contract must stay unchanged for every caller except _run_gauntlet. " +
             "(5) Run the relevant tests (pytest -c cerebral/pytest.ini and/or root python -m pytest) and proceed ONLY if ALL pass. If you launch Cerebral to smoke anything, launch it in the BACKGROUND and ALWAYS terminate it before you finish -- leave no orphan 'python -m cerebral.main' process. " +
             "(6) Open the PR with 'Closes #N' in the body. Merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. If gh reports the PR is not mergeable yet, wait ~15s and retry up to 5 times. " +
             "(7) git checkout master and git pull origin master so master is current. " +
             "(8) Rewrite STRATEGY-REPAIR.md's 'Next slice' block: tick the landed entry in the Queue, set the next unticked entry as Active (its 'SRx -- #N' and 'Model:' line), set 'Status:' (ready while slices remain; done after SR4 lands), and append the merged PR under 'Landed PRs'. Commit this change directly to master and push it. STRATEGY-REPAIR.md is the ONLY thing you may commit straight to master -- all code goes through the PR. " +
             "(9) If tests fail and you cannot fix them, or the slice genuinely needs a human, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging the PR. " +
             "(10) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Strategy Repair loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Strategy Repair loop" "STRATEGY-REPAIR.md says Status: done. All slices landed."; break }
        if ($status -eq "blocked") { Notify "Strategy Repair loop" "STRATEGY-REPAIR.md says Status: blocked. A session needs your input - read STRATEGY-REPAIR.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in STRATEGY-REPAIR.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read STRATEGY-REPAIR.md and complete the active slice exactly as specced in its own issue. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in STRATEGY-REPAIR.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, make sure no orphan Cerebral process is running, run the test suite, " +
                          "find and fix the problem, and finish the slice. " + $rules
            }

            $outLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.out.log" -f $runStamp, $slice, $attempt)
            $errLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.err.log" -f $runStamp, $slice, $attempt)

            Log ("slice {0} attempt {1}/{2} starting (log: {3})" -f $slice, $attempt, $MaxAttempts, $outLog)

            $pyBefore = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*cerebral.main*' } |
                Select-Object -ExpandProperty ProcessId)

            $argString = '-p --model {0} --dangerously-skip-permissions "{1}"' -f $model, $prompt
            $proc = Start-Process -FilePath $claudeCmd -ArgumentList $argString `
                -WorkingDirectory $repoRoot -NoNewWindow -PassThru `
                -RedirectStandardOutput $outLog -RedirectStandardError $errLog
            $null = $proc.Handle

            $maxRunSec = 5400
            $polled = 0
            while (-not $proc.HasExited) {
                if (Test-Path $stopFile) {
                    Log ("slice {0}: STOP file found, killing the running session" -f $slice)
                    try { $proc.Kill() } catch {}
                    break
                }
                if ($polled -ge $maxRunSec) {
                    Log ("slice {0}: attempt exceeded {1}s, killing the session" -f $slice, $maxRunSec)
                    try { $proc.Kill() } catch {}
                    break
                }
                Start-Sleep -Seconds 5
                $polled += 5
            }
            try { $proc.WaitForExit() } catch {}

            Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*cerebral.main*' -and $pyBefore -notcontains $_.ProcessId } |
                ForEach-Object {
                    Log ("slice {0}: reaping orphan Cerebral pid {1}" -f $slice, $_.ProcessId)
                    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
                }

            $output = ""
            if (Test-Path $outLog) { $output += [IO.File]::ReadAllText($outLog) }
            if (Test-Path $errLog) { $output += [IO.File]::ReadAllText($errLog) }

            if ($output -match $limitPattern) {
                if ($output -match "Not logged in|Failed to authenticate") {
                    Notify "Strategy Repair loop" "The claude CLI is not logged in (or its token expired). Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Strategy Repair loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Strategy Repair loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Strategy Repair loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
                    $(if ($resetTxt) { " ($resetTxt)" } else { "" }), $resumeAt) 30

                $sleptFull = Wait-WithStopCheck $waitSec $stopFile
                if (-not $sleptFull) { Log "STOP file found during limit wait, ending loop."; $stopAll = $true; break }

                Log ("slice {0}: resuming after limit wait, retrying attempt {1}" -f $slice, $attempt)
                $attempt--
                continue
            }

            $limitWaits = 0

            $exitCode = $null
            try { $exitCode = $proc.ExitCode } catch { $exitCode = $null }

            if ($exitCode -eq 0) {
                Log ("slice {0} attempt {1} succeeded" -f $slice, $attempt)
                $succeeded = $true
                break
            }

            Log ("slice {0} attempt {1} FAILED (exit {2})" -f $slice, $attempt, $(if ($null -eq $exitCode) { "unknown" } else { $exitCode }))
        }

        if ($stopAll) { break }

        if (-not $succeeded) {
            Notify "Strategy Repair loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\strategy-repair-loop and STRATEGY-REPAIR.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Strategy Repair loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    # Read-Host throws under -NonInteractive (e.g. invoked by an automation
    # harness rather than double-clicked) -- swallow it so a clean run isn't
    # misreported as a failure just because there's no console to pause.
    try { Read-Host "Press Enter to close" | Out-Null } catch {}
}
