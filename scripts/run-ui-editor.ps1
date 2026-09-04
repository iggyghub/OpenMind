# run-ui-editor.ps1 -- autonomous slice loop for the UI Editor tool campaign.
#
# Repeats: read UI-EDITOR.md 'Next slice' block -> launch a fresh headless Claude
# Code session (claude -p) on the recommended model -> session implements the active
# slice, runs the tests, opens a per-issue PR, MERGES it to master (so the next
# slice branches off a current master and same-file edits to tools/ui-editor/*
# never collide), rewrites the kickoff block -> loop.
#
# Each attempt is a brand-new session (keeps per-slice token count low);
# UI-EDITOR.md is the only memory between them. Logs land in .claude\tmp\ui-editor-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-ui-editor.ps1 (graceful, between steps)
#   - UI-EDITOR.md Status: done     (S6 landed)
#   - UI-EDITOR.md Status: blocked  (a session decided it needs a human)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: this campaign only ever touches tools/ui-editor/** -- never cerebral/,
# plugins/, or tray/ (Felix's real app). Every slice is AFK (auto-merge on green).
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a terminal,
# type /login, complete the browser flow.

param(
    [int]$MaxSlices = 6,
    [int]$MaxAttempts = 3,
    [bool]$AutoResume = $true,
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "64000"

    $driver = Join-Path $repoRoot "UI-EDITOR.md"
    if (-not (Test-Path $driver)) { throw "UI-EDITOR.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\ui-editor-loop"
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
        Notify "UI Editor loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in|Failed to authenticate"

    # Successive slices edit the same tools/ui-editor/inject.js + server.js --
    # each must land on master before the next branches off.
    $rules = "Hard rules, in order: " +
             "(1) Read UI-EDITOR.md first. The active slice is named in the 'Next slice' block as 'Sx -- #N'. Run gh issue view N for its full spec -- the issue body IS the spec, there is no separate ADR for this tool. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b ui-editor/sN-short-name origin/master). " +
             "(3) Implement ONLY that one slice exactly as its issue specifies. One PR per issue. Touch only tools/ui-editor/** -- never cerebral/, plugins/, or tray/ (Felix's real app; this tool only proxies a read of files like tray/windows/main.html at runtime, it must not modify them as a side effect of building/testing). " +
             "(4) No new dependencies without a one-line justification in the PR body -- default to Node stdlib and the built-in `node --test` runner, no bundler, no framework. " +
             "(5) Run `node --test tools/ui-editor/tests/` (create the tests/ dir on S1 if it doesn't exist yet) and proceed ONLY if everything passes. If you start the tool's own server.js to smoke-test manually, run it in the BACKGROUND and ALWAYS kill it before you finish -- leave no orphan node process listening on port 4545. " +
             "(6) Open the PR with 'Closes #N' in the body. Merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. If gh reports the PR is not mergeable yet, wait ~15s and retry up to 5 times. This campaign has no HITL slices -- always auto-merge on green. " +
             "(7) git checkout master and git pull origin master so master is current. " +
             "(8) Rewrite the UI-EDITOR.md 'Next slice' block: tick the landed entry in the Queue, set the next unticked entry as Active (its 'Sx -- #N' and 'Model:' line), set 'Status:' (ready while slices remain; done after S6 lands), and append the merged PR under 'Landed PRs'. Commit the UI-EDITOR.md change directly to master and push it -- it is the ONLY thing you may commit straight to master, all code goes through the PR. " +
             "(9) If tests fail and you cannot fix them, or the slice genuinely needs a human decision, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging the PR. " +
             "(10) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== UI Editor loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "UI Editor loop" "UI-EDITOR.md says Status: done. All slices landed."; break }
        if ($status -eq "blocked") { Notify "UI Editor loop" "UI-EDITOR.md says Status: blocked. A session needs your input - read UI-EDITOR.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in UI-EDITOR.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read UI-EDITOR.md and complete the active slice exactly as specced in its GitHub issue. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in UI-EDITOR.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, make sure no orphan node process is still listening on port 4545, run the test suite, " +
                          "find and fix the problem, and finish the slice. " + $rules
            }

            $outLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.out.log" -f $runStamp, $slice, $attempt)
            $errLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.err.log" -f $runStamp, $slice, $attempt)

            Log ("slice {0} attempt {1}/{2} starting (log: {3})" -f $slice, $attempt, $MaxAttempts, $outLog)

            $nodeBefore = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*ui-editor*server.js*' } |
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

            Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*ui-editor*server.js*' -and $nodeBefore -notcontains $_.ProcessId } |
                ForEach-Object {
                    Log ("slice {0}: reaping orphan ui-editor server pid {1}" -f $slice, $_.ProcessId)
                    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
                }

            $output = ""
            if (Test-Path $outLog) { $output += [IO.File]::ReadAllText($outLog) }
            if (Test-Path $errLog) { $output += [IO.File]::ReadAllText($errLog) }

            if ($output -match $limitPattern) {
                if ($output -match "Not logged in|Failed to authenticate") {
                    Notify "UI Editor loop" "The claude CLI is not logged in (or its token expired). Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "UI Editor loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "UI Editor loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "UI Editor loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
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
            Notify "UI Editor loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\ui-editor-loop and UI-EDITOR.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== UI Editor loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
