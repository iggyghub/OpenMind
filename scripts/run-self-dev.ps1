# run-self-dev.ps1 -- autonomous slice loop for the self-dev-loop campaign (ADR-0015).
#
# Repeats: read SELF-DEV-BUILD.md 'Next slice' block -> launch a fresh headless
# Claude Code session (claude -p) on the recommended model -> session implements
# the active slice's issue as ONE PR with hermetic tests, then (AFK) merges it or
# (HITL) opens it and stops for human review, rewrites the kickoff block -> loop.
#
# Each attempt is a brand-new session (keeps per-slice token count low);
# SELF-DEV-BUILD.md + the issue are the only memory between them. Logs land in
# .claude\tmp\self-dev-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-self-dev.ps1 (graceful, between steps)
#   - SELF-DEV-BUILD.md Status: done      (SD-4 landed)
#   - SELF-DEV-BUILD.md Status: blocked   (an HITL slice opened a PR for review,
#                                          or a session needs a human)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: slices BUILD the self-dev machinery. A slice must NOT run a real
# self-modification against the live repo, start a real Cerebral, or open real
# PRs from inside tests -- every test injects all side effects (clone/edit/test/pr).
# HITL slices (SD-3, SD-4) touch the guardrails: they open a PR and STOP for human
# review, never self-merge (ADR-0015 blast-radius gate).
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

    $driver = Join-Path $repoRoot "SELF-DEV-BUILD.md"
    if (-not (Test-Path $driver)) { throw "SELF-DEV-BUILD.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\self-dev-loop"
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
        Notify "Self-dev loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in|Failed to authenticate"

    $rules = "Hard rules, in order: " +
             "(1) Read SELF-DEV-BUILD.md, docs/adr/0015-self-dev-loop.md, and the CONTEXT.md terms (self-dev loop, blast-radius gate, boot self-check + rollback) first. The active slice is named in the 'Next slice' block as 'SD-x -- #N' with a 'Type:' of AFK or HITL. Run gh issue view N for its full acceptance criteria. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b selfdev/sdN-short-name origin/master). " +
             "(3) Implement ONLY that one slice, end to end with tests, as one PR. Follow CLAUDE.md and ADR-0005/0010/0015. Mirror the conventions in plugins/builder.py and plugins/skills.py (module-level PLUGIN_NAME + REQUIRED_CAPABILITIES from the closed 16-class vocabulary, list_tools/call_tool/create, Tool/ToolResult from cerebral.mcp.orchestrator). Mirror builder.py's injected-side-effect pattern so the flow tests hermetically. " +
             "(4) SAFETY (highest priority): BUILD the machinery, do not RUN it against the live system. No test may shell out to real git/gh/network or start a real Cerebral -- inject clone_fn/edit_fn/test_fn/pr_fn (and any model call) and use fakes. Do not open a real PR from a test. Keep all file bodies ASCII (Windows PS 5.1 reads .ps1 as ANSI -- see CLAUDE.md). " +
             "(5) Verify: run the slice's own pytest (python -m pytest <the new test file> -q) and it must pass; also do a syntax/import sanity check on any new module. " +
             "(6) Ground the code in this repo's real conventions -- read the files the issue names (plugins/builder.py, plugins/skills.py, plugins/shell.py, cerebral/llm/router.py, cerebral/mcp/orchestrator.py, cerebral/security) so the code integrates, not guesses. " +
             "(7) Open the PR with 'Closes #N' in the body. THEN branch on Type: " +
             "  * Type: AFK -> merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. If gh reports not mergeable yet, wait ~15s and retry up to 5 times. " +
             "  * Type: HITL -> DO NOT merge. Leave the PR open. Set SELF-DEV-BUILD.md 'Status: blocked' with a one-line reason naming the PR number ('SD-x PR #<pr> awaiting human review'), commit that to master, and stop. " +
             "(8) git checkout master and git pull origin master so master is current. " +
             "(9) Rewrite the SELF-DEV-BUILD.md 'Next slice' block: tick the landed entry in the Queue, set the next unticked+unblocked entry as Active (its 'SD-x -- #N', 'Model:' and 'Type:' lines), set 'Status:' (ready while AFK slices remain; blocked if you just opened an HITL PR; done after SD-4 lands), and append the merged PR (AFK only) under 'Landed PRs'. " +
             "Commit the SELF-DEV-BUILD.md change directly to master and push it. SELF-DEV-BUILD.md is the ONLY thing you may commit straight to master -- all slice code goes through the PR. " +
             "(10) If the slice genuinely needs a human decision, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging. " +
             "(11) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Self-dev loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Self-dev loop" "SELF-DEV-BUILD.md says Status: done. All slices landed."; break }
        if ($status -eq "blocked") { Notify "Self-dev loop" "SELF-DEV-BUILD.md says Status: blocked. An HITL PR is awaiting your review (or a session needs input) - read SELF-DEV-BUILD.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in SELF-DEV-BUILD.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read SELF-DEV-BUILD.md and implement the active slice (SD-x -- #N) exactly as specced in its issue, end to end with hermetic tests. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in SELF-DEV-BUILD.md. " -f $attempt, $MaxAttempts) +
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
                    Notify "Self-dev loop" "The claude CLI is not logged in (or its token expired). Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Self-dev loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Self-dev loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Self-dev loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
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
            Notify "Self-dev loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\self-dev-loop and SELF-DEV-BUILD.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Self-dev loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
