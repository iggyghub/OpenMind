# run-boards.ps1 -- autonomous slice loop for the Job Boards campaign.
#
# Repeats: read BOARDS.md 'Next slice' block -> launch a fresh headless Claude
# Code session (claude -p) on the recommended model -> session implements the
# active slice against FAKES/fixtures (never a live board or ATS), runs tests,
# opens a per-issue PR, MERGES it to master (so the next slice branches off a
# current master and same-file edits to plugins/job_search.py never collide),
# rewrites the kickoff block -> loop.
#
# Each attempt is a brand-new session; BOARDS.md + CONTEXT.md + ADR-0009 are
# the only memory between them. Logs land in .claude\tmp\boards-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-boards.ps1 (graceful, between steps)
#   - BOARDS.md Status: done     (S1..S2 landed)
#   - BOARDS.md Status: blocked  (a session decided it needs a human)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: the loop builds + unit-tests slices only. It must never fetch a live
# job board, drive a real ATS, or use real credentials -- enforced in the
# prompt rules below and in BOARDS.md "SAFETY".
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

    $driver = Join-Path $repoRoot "BOARDS.md"
    if (-not (Test-Path $driver)) { throw "BOARDS.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\boards-loop"
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
                # The reset clock time is the NEXT occurrence. If it has already
                # passed today (e.g. "2am" seen at 10pm), roll it to tomorrow --
                # otherwise the target is in the past and we'd thrash on the 300s
                # floor, re-hitting the limit every 5 min instead of waiting.
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
        Notify "Boards loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in"

    # Each slice MUST land on master before the next starts: S2 builds on S1's
    # job_boards table, fetch loop, and panel section.
    $rules = "Hard rules, in order: " +
             "(1) Read CONTEXT.md 'Job-application pipeline' glossary, docs/adr/0009-job-application-automation.md, and BOARDS.md first. The active slice is named in the 'Next slice' block as 'Sx -- #N'. Run gh issue view N for its detail. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b boards/sN-short-name origin/master). " +
             "(3) Implement ONLY that one slice exactly as the issue specifies. One PR per issue. Code areas: plugins/job_search.py (job_boards table + fetch loop + extractor seam), the Job Search panel in tray/windows/main.html, and cerebral/main.py (IPC handlers + _wire_plugin_seams). " +
             "(4) SAFETY (highest priority): build and unit-test against FAKES only -- saved HTML fixtures, a stubbed navigate fn, a stubbed LLM extractor fn. NO live network fetch of any job board or ATS, NO real credentials, NO real submissions, NO real inbox. For any behaviour only checkable live, APPEND a checklist item to docs/jobs-live-verify.md -- do NOT perform it. " +
             "(5) Seam rule (#153/#385): NEVER 'from plugins.job_search import set_*' anywhere in cerebral/ -- wire seams through _wire_plugin_seams / _js_seam against _orc.get_plugin_module('job_search'). cerebral/tests/test_jobs_seam_wiring.py guards this; keep it passing. Store writes must coerce LLM-shaped input (explicit nulls -> column defaults) and roll back on failure (#388 precedent). If the tray renders from a client-side list mirroring a server-side list, comment the pairing on BOTH sides (#390 lesson). " +
             "(6) Run the relevant tests (python -m pytest cerebral/tests -q, plus npx jest in tray/ when the panel changed) and proceed ONLY if ALL pass. If you launch Cerebral to smoke IPC, launch it in the BACKGROUND and ALWAYS terminate it before you finish -- leave no orphan 'python -m cerebral.main' process. " +
             "(7) Open the PR with 'Closes #N' in the body. Merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. If gh reports the PR is not mergeable yet, wait ~15s and retry up to 5 times. If --delete-branch fails because master is checked out elsewhere, merge without it and delete the remote branch with git push origin --delete. " +
             "(8) git checkout master and git pull origin master so master is current. " +
             "(9) Rewrite the BOARDS.md 'Next slice' block: tick the landed entry in the queue, set the next unticked entry as the Active slice (its 'Sx -- #N' and its 'Model:' line), set 'Status:' (ready while S1 remains; done after S2 lands), and add the merged PR under 'Landed PRs'. Commit the BOARDS.md change directly to master and push it. BOARDS.md is the ONLY thing you may commit straight to master -- all code goes through the PR. " +
             "(10) If tests fail and you cannot fix them, or the slice genuinely needs a human / live action, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging the PR. " +
             "(11) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Boards loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Boards loop" "BOARDS.md says Status: done. All slices landed."; break }
        if ($status -eq "blocked") { Notify "Boards loop" "BOARDS.md says Status: blocked. A session needs your input - read BOARDS.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in BOARDS.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read BOARDS.md and complete the active slice exactly as specced in its issue. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in BOARDS.md. " -f $attempt, $MaxAttempts) +
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

            $env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "64000"
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
                    Notify "Boards loop" "The claude CLI is not logged in. Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Boards loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Boards loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Boards loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
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
            Notify "Boards loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\boards-loop and BOARDS.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Boards loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
