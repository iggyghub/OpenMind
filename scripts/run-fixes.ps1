# run-fixes.ps1 -- autonomous run-and-fix loop for OpenMind.
#
# The run-and-fix analogue of run-slices.ps1. Repeats: read FIX-LOG.md kickoff
# block -> launch a fresh headless Claude Code session (claude -p) on the
# recommended model -> session boots Cerebral, smokes it for runtime bugs,
# fixes ONE on the review branch 'fix/run-campaign', appends a log entry, and
# rewrites the kickoff block (Model + Status) for the next session -> loop.
#
# IMPORTANT: this loop NEVER commits or merges to master. Every fix accumulates
# on the long-lived branch 'fix/run-campaign' so a human can review the whole
# campaign and merge it deliberately afterwards. Master stays untouched.
#
# Stop conditions:
#   - STOP file created by scripts/stop-fixes.ps1 (graceful, between steps)
#   - FIX-LOG.md Status: clean   (a smoke pass found nothing left to fix)
#   - FIX-LOG.md Status: blocked (a session decided a bug needs a human)
#   - FIX-LOG.md Status: done    (manual halt)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - An iteration fails 3 attempts in a row (debug retries exhausted)
#   - MaxIterations safety cap
# Closing this console window is the hard stop (kills the running step).
#
# Each attempt is a brand-new session; FIX-LOG.md is the only memory between
# them. Logs land in .claude\tmp\fix-loop\.
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a
# terminal, type /login, complete the browser flow.

param(
    [int]$MaxIterations = 8,
    [int]$MaxAttempts = 3,
    # When the usage limit is hit, sleep until it resets and auto-resume instead
    # of stopping. -AutoResume:$false reverts to stop-and-notify.
    [bool]$AutoResume = $true,
    # Safety cap on consecutive limit-waits, so a permanent block can't loop forever.
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $stateDir = Join-Path $repoRoot ".claude\tmp\fix-loop"
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

    function Get-LogField($name, $default) {
        $m = Select-String -LiteralPath (Join-Path $repoRoot "FIX-LOG.md") `
            -Pattern ("^{0}:\s*(\S+)" -f $name) | Select-Object -First 1
        if ($null -eq $m) { return $default }
        return $m.Matches[0].Groups[1].Value.ToLower()
    }

    $claudeCmd = Get-Command claude.cmd -ErrorAction SilentlyContinue
    if ($null -eq $claudeCmd) {
        Notify "Fix loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    # Matches the actual CLI wording, e.g. "You've hit your limit . resets 7:20pm",
    # plus rate-limit and not-logged-in variants. Keep broad: a missed match here
    # turns a benign cap into 3 wasted instant retries + a false "failed" alarm.
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in"

    # Run-and-fix flow: each session boots+smokes Cerebral, fixes ONE bug on the
    # review branch fix/run-campaign (NEVER master), then records the result so the
    # next fresh session can pick up where this one left off. A human reviews and
    # merges the whole campaign afterwards.
    $rules = "Hard rules, in order: " +
             "(1) Read all of FIX-LOG.md first so you do not re-hunt a bug a previous session already fixed " +
             "(including fixes already on the campaign branch but not yet reviewed). " +
             "(2) NEVER commit or merge to master. All work lands on a single long-lived review branch named 'fix/run-campaign'. " +
             "git fetch origin; if that branch exists (locally or as origin/fix/run-campaign) git checkout fix/run-campaign and git pull, " +
             "otherwise git checkout -b fix/run-campaign origin/master. " +
             "(3) Boot Cerebral headless (python -m cerebral.main, WebSocket IPC on ws://localhost:7766) and SMOKE it: " +
             "exercise tool dispatch / IPC the way cerebral/tests rigs do (orchestrator, settings, conversation, queue) " +
             "and watch cerebral.err.log + stdout for unhandled exceptions and tracebacks. " +
             "Launch Cerebral in the BACKGROUND (never as a blocking foreground command -- it runs forever) and ALWAYS " +
             "terminate it before you finish, even on error -- leave no orphan python -m cerebral.main process. " +
             "(4) SAFETY: never run plugins/discord_user.py or the Discord self-bot path (real-account ban risk per ADR-0006). " +
             "No real credentials, no live OAuth, no real messages/calls/paid APIs -- mocked/local backends and offline fallbacks only. " +
             "The voice/mic, browser-OAuth, and 8-hour real-time parts of docs/v1-live-verify.md are human-only and out of scope. " +
             "(5) Find ONE real runtime bug. If found: fix it on fix/run-campaign, run the relevant tests " +
             "(pytest -c cerebral/pytest.ini, or root python -m pytest); proceed ONLY if they pass. " +
             "(6) Commit the fix to fix/run-campaign with a clear message and push (git push -u origin fix/run-campaign). DO NOT merge. " +
             "Ensure exactly one open PR exists for the branch: gh pr view fix/run-campaign, else gh pr create --draft " +
             "--base master --head fix/run-campaign --title 'Run-and-fix campaign' (file a GitHub issue with label needs-triage if the bug warrants tracking). " +
             "NEVER merge the PR -- it stays open for human review. " +
             "(7) Append a Log entry to FIX-LOG.md (what you ran, the bug, the fix, the commit SHA) and rewrite the kickoff block: " +
             "the 'Model:' line (haiku|sonnet|opus|fable -- prefer opus for hard diagnosis, sonnet for mechanical fixes) and " +
             "the 'Status:' line (hunting if you fixed something; clean if a FULL smoke pass found NOTHING to fix; " +
             "blocked + a one-line reason if you found a bug you cannot fix). " +
             "Commit the FIX-LOG.md change to fix/run-campaign and push. " +
             "(8) Leave the working tree on fix/run-campaign with no uncommitted changes before you finish. Master must remain untouched."

    Log ("=== fix loop started (max {0} iterations, {1} attempts each, auto-resume={2}) ===" -f $MaxIterations, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($iter = 1; $iter -le $MaxIterations -and -not $stopAll; $iter++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-LogField "Status" "hunting"
        if ($status -eq "clean")   { Notify "Fix loop" "FIX-LOG says Status: clean. A smoke pass found nothing left to fix."; break }
        if ($status -eq "done")    { Notify "Fix loop" "FIX-LOG says Status: done. Loop halted."; break }
        if ($status -eq "blocked") { Notify "Fix loop" "FIX-LOG says Status: blocked. A session needs your input - read FIX-LOG.md."; break }

        $model = Get-LogField "Model" "opus"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in FIX-LOG.md, falling back to opus" -f $model)
            $model = "opus"
        }

        Log ("--- iteration {0}: model={1} ---" -f $iter, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read FIX-LOG.md and run one run-and-fix iteration exactly as specced in its kickoff block. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the current fix iteration. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, make sure no orphan Cerebral process is running, run the test suite, find and " +
                          "fix the problem, and finish the iteration. " + $rules
            }

            $outLog = Join-Path $stateDir ("{0}-iter{1}-attempt{2}.out.log" -f $runStamp, $iter, $attempt)
            $errLog = Join-Path $stateDir ("{0}-iter{1}-attempt{2}.err.log" -f $runStamp, $iter, $attempt)

            Log ("iteration {0} attempt {1}/{2} starting (log: {3})" -f $iter, $attempt, $MaxAttempts, $outLog)

            # Snapshot any cerebral.main already running (e.g. the user's own Felix) so
            # we only reap Cerebrals THIS attempt leaves behind, never a pre-existing one.
            $pyBefore = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*cerebral.main*' } |
                Select-Object -ExpandProperty ProcessId)

            $argString = '-p --model {0} --dangerously-skip-permissions "{1}"' -f $model, $prompt
            # Do NOT use Start-Process -Wait here. The session launches Cerebral
            # (python -m cerebral.main); if it leaves one running -- e.g. it hits the usage
            # limit or crashes mid-run -- that child inherits the redirected stdout handle
            # and -Wait blocks on the stream EOF forever. Launch detached, poll the claude
            # process by handle, then reap any orphan Cerebral so it can't wedge the loop.
            $proc = Start-Process -FilePath $claudeCmd -ArgumentList $argString `
                -WorkingDirectory $repoRoot -NoNewWindow -PassThru `
                -RedirectStandardOutput $outLog -RedirectStandardError $errLog

            $maxRunSec = 5400   # 90-min hard cap per attempt, so a hung session can't stall forever
            $polled = 0
            while (-not $proc.HasExited) {
                if (Test-Path $stopFile) {
                    Log ("iteration {0}: STOP file found, killing the running session" -f $iter)
                    try { $proc.Kill() } catch {}
                    break
                }
                if ($polled -ge $maxRunSec) {
                    Log ("iteration {0}: attempt exceeded {1}s, killing the session" -f $iter, $maxRunSec)
                    try { $proc.Kill() } catch {}
                    break
                }
                Start-Sleep -Seconds 5
                $polled += 5
            }
            try { $proc.WaitForExit() } catch {}

            # Reap any Cerebral this attempt spawned but did not stop, so it can neither
            # hold the redirected stdout handle nor wedge the next iteration.
            Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*cerebral.main*' -and $pyBefore -notcontains $_.ProcessId } |
                ForEach-Object {
                    Log ("iteration {0}: reaping orphan Cerebral pid {1}" -f $iter, $_.ProcessId)
                    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
                }

            $output = ""
            if (Test-Path $outLog) { $output += [IO.File]::ReadAllText($outLog) }
            if (Test-Path $errLog) { $output += [IO.File]::ReadAllText($errLog) }

            if ($output -match $limitPattern) {
                # Not-logged-in is unrecoverable without a human -> always stop.
                if ($output -match "Not logged in") {
                    Notify "Fix loop" "The claude CLI is not logged in. Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Fix loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Fix loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("iteration {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $iter, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Fix loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
                    $(if ($resetTxt) { " ($resetTxt)" } else { "" }), $resumeAt) 30

                $sleptFull = Wait-WithStopCheck $waitSec $stopFile
                if (-not $sleptFull) { Log "STOP file found during limit wait, ending loop."; $stopAll = $true; break }

                Log ("iteration {0}: resuming after limit wait, retrying attempt {1}" -f $iter, $attempt)
                $attempt--   # do not consume a debug attempt: the cap was not a code failure
                continue
            }

            # A successful run means we are not limit-blocked; reset the wait counter.
            $limitWaits = 0

            if ($proc.ExitCode -eq 0) {
                Log ("iteration {0} attempt {1} succeeded" -f $iter, $attempt)
                $succeeded = $true
                break
            }

            Log ("iteration {0} attempt {1} FAILED (exit {2})" -f $iter, $attempt, $proc.ExitCode)
        }

        if ($stopAll) { break }

        if (-not $succeeded) {
            Notify "Fix loop" ("Iteration failed after {0} attempts. Check the logs in .claude\tmp\fix-loop and FIX-LOG.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== fix loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
