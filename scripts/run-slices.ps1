# run-slices.ps1 -- autonomous slice loop for OpenMind development.
#
# Repeats: read HANDOFF.md kickoff block -> launch a fresh headless Claude
# Code session (claude -p) on the recommended model -> session completes the
# slice, runs tests, opens a PR, MERGES it to master (so the next slice
# branches off a current master and same-file edits never collide), then
# rewrites the kickoff block for the next slice -> loop.
#
# Stop conditions:
#   - STOP file created by scripts/stop-slices.ps1 (graceful, between steps)
#   - HANDOFF.md Status: done  (all planned work finished)
#   - HANDOFF.md Status: blocked (a session decided it needs a human)
#   - Claude subscription usage limit reached (detected in output)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# Each attempt is a brand-new session; HANDOFF.md is the only memory
# between them. Logs land in .claude\tmp\slice-loop\.
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a
# terminal, type /login, complete the browser flow.

param(
    # Default covers the full 16-slice v1 queue in HANDOFF.md plus headroom.
    [int]$MaxSlices = 25,
    [int]$MaxAttempts = 3,
    # When the usage limit is hit, sleep until it resets and auto-resume the
    # same slice instead of stopping. -AutoResume:$false reverts to stop-and-notify.
    [bool]$AutoResume = $true,
    # Safety cap on consecutive limit-waits, so a permanent block can't loop forever.
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $stateDir = Join-Path $repoRoot ".claude\tmp\slice-loop"
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
        # 48 = exclamation icon; 0 = wait until dismissed (use for terminal stops)
        $shell.Popup($msg, 0, $title, 48) | Out-Null
    }
    function NotifyTimed($title, $msg, $seconds) {
        Log ("NOTIFY: {0} - {1}" -f $title, $msg)
        # 64 = info icon; auto-dismisses after $seconds so it never blocks an unattended wait
        $shell.Popup($msg, $seconds, $title, 64) | Out-Null
    }

    # Parse "...resets 7:20pm..." into seconds-from-now until that clock time.
    # Caps at 5h30m (the max usage window) and adds a 2-min buffer past reset.
    # Returns a sane default (5h15m) when no time can be parsed.
    function Get-SecondsUntilReset($text) {
        $buffer = 120
        $capSec = 19800            # 5h30m
        $default = 18900           # 5h15m
        if ($text -match 'resets?\s+(\d{1,2}(:\d{2})?\s*[ap]\.?m\.?)') {
            $clock = $matches[1] -replace '\.', ''
            try {
                $target = [DateTime]::Parse($clock)          # today at that clock time
                $now = Get-Date
                if ($target -le $now.AddMinutes(1)) {
                    # reset time is now/just passed -> retry shortly
                    return 300
                }
                $sec = [int]($target - $now).TotalSeconds + $buffer
                if ($sec -gt $capSec) { return $capSec }
                return $sec
            } catch { return $default }
        }
        return $default
    }

    # Sleep $seconds in 60s ticks, bailing early if the STOP file appears.
    # Returns $true if it slept the full duration, $false if STOP interrupted it.
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

    function Get-HandoffField($name, $default) {
        $m = Select-String -LiteralPath (Join-Path $repoRoot "HANDOFF.md") `
            -Pattern ("^{0}:\s*(\S+)" -f $name) | Select-Object -First 1
        if ($null -eq $m) { return $default }
        return $m.Matches[0].Groups[1].Value.ToLower()
    }

    $claudeCmd = Get-Command claude.cmd -ErrorAction SilentlyContinue
    if ($null -eq $claudeCmd) {
        Notify "Slice loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    # Matches the actual CLI wording, e.g. "You've hit your limit . resets 7:20pm",
    # plus rate-limit and not-logged-in variants. Keep broad: a missed match here
    # turns a benign cap into 3 wasted instant retries + a false "failed" alarm.
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in"

    # Merge-between-slices flow: each slice MUST land on master before the next
    # one starts, otherwise parallel slices that all edit the same big file (e.g.
    # tray/windows/main.html) diverge off the same base and collide at merge time.
    $rules = "Hard rules, in order: " +
             "(1) Branch off the latest origin/master (git fetch then git checkout -b <branch> origin/master). " +
             "(2) One PR per issue. Implement the slice. " +
             "(3) Run the relevant tests; proceed ONLY if they pass. " +
             "(4) Open the PR with 'Closes #N' in the body. " +
             "(5) Merge YOUR OWN PR into master now: gh pr merge <n> --squash --delete-branch. " +
             "If gh reports the PR is not mergeable yet, wait ~15s and retry up to 5 times " +
             "(GitHub recomputes mergeability after the push). " +
             "(6) git checkout master and git pull origin master so master is current. " +
             "(7) Rewrite the HANDOFF.md kickoff block for the NEXT slice: the 'Model:' line " +
             "(haiku|sonnet|opus|fable -- prefer sonnet on this plan) and the 'Status:' line " +
             "(ready|blocked|done). Commit that HANDOFF change directly to master and push it. " +
             "The HANDOFF update is the ONLY thing you may commit straight to master -- all code goes through the PR. " +
             "If tests fail and you cannot fix them, set Status: blocked with a one-line reason, " +
             "commit that to master, and stop WITHOUT merging the PR. " +
             "Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== slice loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-HandoffField "Status" "ready"
        if ($status -eq "done")    { Notify "Slice loop" "HANDOFF says Status: done. All planned work finished."; break }
        if ($status -eq "blocked") { Notify "Slice loop" "HANDOFF says Status: blocked. A session needs your input - read HANDOFF.md."; break }

        $model = Get-HandoffField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in HANDOFF.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read HANDOFF.md and complete the next slice exactly as specced. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the current slice in HANDOFF.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, run the test suite, find and fix the problem, and finish the slice. " + $rules
            }

            $outLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.out.log" -f $runStamp, $slice, $attempt)
            $errLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.err.log" -f $runStamp, $slice, $attempt)

            Log ("slice {0} attempt {1}/{2} starting (log: {3})" -f $slice, $attempt, $MaxAttempts, $outLog)

            $argString = '-p --model {0} --dangerously-skip-permissions "{1}"' -f $model, $prompt
            $proc = Start-Process -FilePath $claudeCmd -ArgumentList $argString `
                -WorkingDirectory $repoRoot -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $outLog -RedirectStandardError $errLog

            $output = ""
            if (Test-Path $outLog) { $output += [IO.File]::ReadAllText($outLog) }
            if (Test-Path $errLog) { $output += [IO.File]::ReadAllText($errLog) }

            if ($output -match $limitPattern) {
                # Not-logged-in is unrecoverable without a human -> always stop.
                if ($output -match "Not logged in") {
                    Notify "Slice loop" "The claude CLI is not logged in. Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Slice loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Slice loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Slice loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming this slice. Closing this window cancels." -f `
                    $(if ($resetTxt) { " ($resetTxt)" } else { "" }), $resumeAt) 30

                $sleptFull = Wait-WithStopCheck $waitSec $stopFile
                if (-not $sleptFull) { Log "STOP file found during limit wait, ending loop."; $stopAll = $true; break }

                Log ("slice {0}: resuming after limit wait, retrying attempt {1}" -f $slice, $attempt)
                $attempt--   # do not consume a debug attempt: the cap was not a code failure
                continue
            }

            # A successful run means we are not limit-blocked; reset the wait counter.
            $limitWaits = 0

            if ($proc.ExitCode -eq 0) {
                Log ("slice {0} attempt {1} succeeded" -f $slice, $attempt)
                $succeeded = $true
                break
            }

            Log ("slice {0} attempt {1} FAILED (exit {2})" -f $slice, $attempt, $proc.ExitCode)
        }

        if ($stopAll) { break }

        if (-not $succeeded) {
            Notify "Slice loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\slice-loop and HANDOFF.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== slice loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
