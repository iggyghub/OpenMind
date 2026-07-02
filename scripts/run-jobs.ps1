# run-jobs.ps1 -- autonomous slice loop for the Job-Application Pipeline campaign.
#
# Repeats: read JOBS.md 'Next slice' block -> launch a fresh headless Claude Code
# session (claude -p) on the recommended model -> session implements the active
# slice against FAKES/fixtures (never a real ATS), runs tests, opens a per-issue
# PR, MERGES it to master (so the next slice branches off a current master and
# same-file edits to plugins/job_search.py never collide), rewrites the kickoff
# block -> loop.
#
# Each attempt is a brand-new session (keeps per-slice token count low); JOBS.md
# + CONTEXT.md + docs/adr/0009-job-application-automation.md are the only memory
# between them. Logs land in .claude\tmp\jobs-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-jobs.ps1 (graceful, between steps)
#   - JOBS.md Status: done     (S1..S7 landed; S8 is optional/human-triggered)
#   - JOBS.md Status: blocked  (a session decided it needs a human / live action)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: the loop builds + unit-tests slices only. It must never drive a real
# ATS, submit a real application, create a real account, or use real credentials
# -- that is enforced in the prompt rules below and in JOBS.md "SAFETY".
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a
# terminal, type /login, complete the browser flow.

param(
    [int]$MaxSlices = 8,
    [int]$MaxAttempts = 3,
    [bool]$AutoResume = $true,
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $driver = Join-Path $repoRoot "JOBS.md"
    if (-not (Test-Path $driver)) { throw "JOBS.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\jobs-loop"
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
        $capSec = 19800
        $default = 18900
        if ($text -match 'resets?\s+(\d{1,2}(:\d{2})?\s*[ap]\.?m\.?)') {
            $clock = $matches[1] -replace '\.', ''
            try {
                $target = [DateTime]::Parse($clock)
                $now = Get-Date
                if ($target -le $now.AddMinutes(1)) { return 300 }
                $sec = [int]($target - $now).TotalSeconds + $buffer
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
        Notify "Jobs loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in"

    # Each slice MUST land on master before the next starts: successive slices
    # build on the same new files (plugins/job_search.py, the SQLite tables, the
    # Job Search panel, main.py wiring) and unmerged parallel slices collide.
    $rules = "Hard rules, in order: " +
             "(1) Read CONTEXT.md 'Job-application pipeline' glossary, docs/adr/0009-job-application-automation.md, and JOBS.md first. The active slice is named in the 'Next slice' block as 'Sx -- #N'. Run gh issue view N for its detail. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b jobs/sN-short-name origin/master). " +
             "(3) Implement ONLY that one slice exactly as the issue + CONTEXT.md + ADR-0009 specify. One PR per issue. New pipeline code lives in plugins/job_search.py + its SQLite tables + the Job Search panel + cerebral/main.py wiring; keep the seams for the deferred slices. " +
             "(4) SAFETY (highest priority): build and unit-test against FAKES only -- reuse the browser harness FakeDriver seam, stub the LLM/embeddings, use saved HTML/PDF fixtures. NO live network, NO real ATS, NO real submission, NO real account creation, NO real credentials, NO real inbox, NO real login seeding. NEVER run plugins/discord_user.py or any self-bot path. No paid APIs, no real messages/calls. For any behaviour only checkable live (a real Greenhouse/Lever run, real account creation), APPEND a checklist item to docs/jobs-live-verify.md -- do NOT perform it. " +
             "(5) Run the relevant tests (pytest -c cerebral/pytest.ini and/or root python -m pytest, plus any Node lib tests under tray) and proceed ONLY if ALL pass. If you launch Cerebral to smoke IPC, launch it in the BACKGROUND and ALWAYS terminate it before you finish -- leave no orphan 'python -m cerebral.main' process. " +
             "(6) Enforce ADR-0009: the application-submit tool is irreversible=True; true unattended auto-submit exists ONLY in S7 and ONLY under the zero-guessed + opt-in + no-new-eligibility gate, scoped to job_apply_submit alone. Every earlier slice is review-before-submit only. " +
             "(7) Open the PR with 'Closes #N' in the body. Merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. If gh reports the PR is not mergeable yet, wait ~15s and retry up to 5 times (GitHub recomputes mergeability after the push). " +
             "(8) git checkout master and git pull origin master so master is current. " +
             "(9) Rewrite the JOBS.md 'Next slice' block: tick the landed entry in the queue, set the next unticked entry as the Active slice (its 'Sx -- #N' and its 'Model:' line), set 'Status:' (ready while core slices S1..S7 remain; done after S7 lands -- S8 is optional and left for a human), and add the merged PR under 'Landed PRs'. " +
             "Commit the JOBS.md change directly to master and push it. JOBS.md is the ONLY thing you may commit straight to master -- all code goes through the PR. " +
             "(10) If tests fail and you cannot fix them, or the slice genuinely needs a human / live action, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging the PR. " +
             "(11) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Jobs loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Jobs loop" "JOBS.md says Status: done. Core slices landed."; break }
        if ($status -eq "blocked") { Notify "Jobs loop" "JOBS.md says Status: blocked. A session needs your input - read JOBS.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in JOBS.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read JOBS.md and complete the active slice exactly as specced in CONTEXT.md 'Job-application pipeline', docs/adr/0009-job-application-automation.md, and its issue. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in JOBS.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, make sure no orphan Cerebral process is running, run the test suite, " +
                          "find and fix the problem, and finish the slice. " + $rules
            }

            $outLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.out.log" -f $runStamp, $slice, $attempt)
            $errLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.err.log" -f $runStamp, $slice, $attempt)

            Log ("slice {0} attempt {1}/{2} starting (log: {3})" -f $slice, $attempt, $MaxAttempts, $outLog)

            # Snapshot any cerebral.main already running (e.g. the user's own Felix) so
            # we only reap Cerebrals THIS attempt leaves behind, never a pre-existing one.
            $pyBefore = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*cerebral.main*' } |
                Select-Object -ExpandProperty ProcessId)

            $argString = '-p --model {0} --dangerously-skip-permissions "{1}"' -f $model, $prompt
            # Launch detached (not -Wait): a session that leaves Cerebral running would
            # inherit the redirected stdout handle and wedge -Wait on stream EOF forever.
            $proc = Start-Process -FilePath $claudeCmd -ArgumentList $argString `
                -WorkingDirectory $repoRoot -NoNewWindow -PassThru `
                -RedirectStandardOutput $outLog -RedirectStandardError $errLog
            # PS 5.1 quirk: touch .Handle once so .ExitCode is populated after exit.
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

            # Reap any Cerebral this attempt spawned but did not stop.
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
                if ($output -match "Not logged in") {
                    Notify "Jobs loop" "The claude CLI is not logged in. Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Jobs loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Jobs loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Jobs loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
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
            Notify "Jobs loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\jobs-loop and JOBS.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Jobs loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
