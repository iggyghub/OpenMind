# run-delegation.ps1 -- autonomous slice loop for the sub-agent delegation campaign (ADR-0020).
#
# Repeats: read DELEGATION-BUILD.md 'Next slice' block -> launch a fresh headless
# Claude Code session (claude -p) on the recommended model -> session implements
# the active slice's issue as ONE PR with hermetic tests, then (AFK) merges it or
# (HITL) opens it and stops for human review, rewrites the kickoff block -> loop.
#
# Each attempt is a brand-new session (keeps per-slice token count low);
# DELEGATION-BUILD.md + the issue are the only memory between them. Logs land in
# .claude\tmp\delegation-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-delegation.ps1 (graceful, between steps)
#   - DELEGATION-BUILD.md Status: done      (all slices landed)
#   - DELEGATION-BUILD.md Status: blocked   (a HITL slice opened a PR for review)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: no test may call a real model, real git/gh/network, or start a real
# Cerebral -- inject fakes (mirror tests/test_eval_harness.py, tests/test_step_ledger.py).
# HITL slices (S2, S4) touch a guardrail (cerebral/main.py) or add an autonomous
# capability (delegate plugin): they open a PR and STOP for human review, never
# self-merge (ADR-0015 blast-radius gate / ADR-0020).
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

    $driver = Join-Path $repoRoot "DELEGATION-BUILD.md"
    if (-not (Test-Path $driver)) { throw "DELEGATION-BUILD.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\delegation-loop"
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

    function Get-SecondsUntilReset($text) {
        $buffer = 120
        $capSec = 28800
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
        Notify "Delegation loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in|Failed to authenticate"

    $rules = "Hard rules, in order: " +
             "(1) Read DELEGATION-BUILD.md and docs/adr/0020-sub-agent-delegation.md first. The active slice is named in the 'Next slice' block as 'Sx -- #N' with a 'Model:' line; its Type (AFK or HITL) is in the Queue line for that slice. Run gh issue view N for its full acceptance criteria. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b delegation/sN-short-name origin/master). " +
             "(3) Implement ONLY that one slice, end to end with tests, as one PR. Follow CLAUDE.md and ADR-0005/0020. " +
             "(4) SAFETY (highest priority): no test may call a real model, real git/gh/network, or start a real Cerebral -- inject fakes and mirror the injected-side-effect pattern in tests/test_eval_harness.py and tests/test_step_ledger.py. Keep all file bodies ASCII (Windows PS 5.1 reads .ps1 as ANSI -- see CLAUDE.md). " +
             "(5) Verify: run the slice's own pytest (python -m pytest <the new/changed test file> -q) and it must pass; also do an import sanity check on any new module. " +
             "(6) Ground the code in this repo's real conventions -- read the files the issue names (cerebral/llm/subagent.py, cerebral/llm/chain_engine.py, cerebral/llm/planner.py, cerebral/llm/router.py, cerebral/llm/step_ledger.py, cerebral/mcp/orchestrator.py, cerebral/security, and for S4 plugins/skills.py + plugins/builder.py) so the code integrates, not guesses. " +
             "(7) Open the PR with 'Closes #N' in the body. THEN branch on the slice's Type: " +
             "  * Type: AFK -> merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. If gh reports not mergeable yet, wait ~15s and retry up to 5 times. " +
             "  * Type: HITL -> DO NOT merge. Leave the PR open. Set DELEGATION-BUILD.md 'Status: blocked' with a one-line reason naming the PR number ('Sx PR #<pr> awaiting human review'), commit that to master, and stop. " +
             "(8) git checkout master and git pull origin master so master is current. " +
             "(9) Rewrite the DELEGATION-BUILD.md 'Next slice' block: tick the landed entry in the Queue, set the next unticked+unblocked entry as Active (its 'Sx -- #N' and 'Model:' lines), set 'Status:' (ready while AFK slices remain; blocked if you just opened a HITL PR; done after the last slice lands), and append the merged PR (AFK only) under 'Landed PRs'. " +
             "Commit the DELEGATION-BUILD.md change directly to master and push it. DELEGATION-BUILD.md is the ONLY thing you may commit straight to master -- all slice code goes through the PR. " +
             "(10) If the slice genuinely needs a human decision, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging. " +
             "(11) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Delegation loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Delegation loop" "DELEGATION-BUILD.md says Status: done. All slices landed."; break }
        if ($status -eq "blocked") { Notify "Delegation loop" "DELEGATION-BUILD.md says Status: blocked. A HITL PR is awaiting your review (or a session needs input) - read DELEGATION-BUILD.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in DELEGATION-BUILD.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read DELEGATION-BUILD.md and implement the active slice (Sx -- #N) exactly as specced in its issue, end to end with hermetic tests. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in DELEGATION-BUILD.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, run the slice's pytest to see the failure, find and fix the root cause, " +
                          "and finish the slice. " + $rules
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
                    Notify "Delegation loop" "The claude CLI is not logged in (or its token expired). Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Delegation loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Delegation loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Delegation loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
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
            Notify "Delegation loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\delegation-loop and DELEGATION-BUILD.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Delegation loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
