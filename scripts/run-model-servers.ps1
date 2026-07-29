# run-model-servers.ps1 -- autonomous slice loop for Model servers.
#
# Repeats: read MODEL-SERVERS.md 'Next slice' block -> launch a fresh headless
# Claude Code session (claude -p) on the recommended model -> session implements
# the active slice, opens a per-issue PR, merges it to master, rewrites the
# kickoff block -> loop.
#
# Stop conditions:
#   - STOP file created by scripts/stop-model-servers.ps1 (graceful, between steps)
#   - MODEL-SERVERS.md Status: done    (all slices landed)
#   - MODEL-SERVERS.md Status: blocked (a session decided it needs a human; S4 is HITL)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# Each attempt is a brand-new session; MODEL-SERVERS.md is the only memory
# between them. Logs land in .claude\tmp\model-servers-loop\.
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a
# terminal, type /login, complete the browser flow. ALSO: the custom-model
# base (custom_models.py, add_custom_model IPC, router.add_backend) must be
# merged to master -- S1-S4 build on it.

param(
    [int]$MaxSlices = 20,
    [int]$MaxAttempts = 3,
    [bool]$AutoResume = $true,
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "64000"
    # Force subscription auth: a User-level ANTHROPIC_API_KEY would make headless
    # sessions bill API credits, which the limit/auto-resume machinery does not model.
    Remove-Item Env:ANTHROPIC_API_KEY -ErrorAction SilentlyContinue

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $driver = Join-Path $repoRoot "MODEL-SERVERS.md"
    if (-not (Test-Path $driver)) { throw "MODEL-SERVERS.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\model-servers-loop"
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
        $capSec = 28800   # 8h ceiling
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
        # Tolerate markdown on the field line: "## Status: done", "- **Model:** opus".
        # Anchor after any leading #/-/*/space and allow ** around the colon. Queue
        # lines ("- [ ] ... (Model: opus)") start with "[" after the dash, so the
        # ^[\s#\-\*]* anchor never reaches their inline "Model:" -- no false match.
        $m = Select-String -LiteralPath $driver `
            -Pattern ("^[\s#\-\*]*{0}\s*:\s*\**\s*(\S+)" -f $name) | Select-Object -First 1
        if ($null -eq $m) { return $default }
        return $m.Matches[0].Groups[1].Value.ToLower()
    }

    $claudeCmd = Get-Command claude.cmd -ErrorAction SilentlyContinue
    if ($null -eq $claudeCmd) {
        Notify "Model servers loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Credit balance is too low|Not logged in|Failed to authenticate"

    $rules = "Hard rules, in order: " +
             "(1) Read MODEL-SERVERS.md (including the SAFETY block) first. The active slice is named in the 'Next slice' block as 'Sx -- #N'. Run gh issue view N for its full spec. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b model-servers/sN-short-name origin/master). " +
             "(3) Implement ONLY that one slice exactly as the issue specifies. One PR per issue. Code areas: cerebral/llm/router.py, cerebral/main.py (IPC handlers + seams), cerebral/db/custom_models.py, cerebral/tests/*, tray/windows/main.html, tray/lib/*.js, and (S4 only) plugins/. " +
             "(4) SAFETY (highest priority, overrides finishing the slice): obey the MODEL-SERVERS.md SAFETY block -- NO live calls to bonsai.ai-dabs.com or any external LLM/HTTP endpoint in tests (inject a fake fetch/client like the OllamaBackend tags_fetch_fn / AnthropicBackend client seams), never commit/print/persist a real API key, behaviour only checkable against a real remote server -> APPEND to docs/model-servers-live-verify.md and do NOT perform it. If the active slice is S4 (#526): do NOT implement it, set Status: blocked (reason: S4 needs human security review), commit the driver, and STOP without a PR. " +
             "(5) Run the relevant tests (python -m pytest cerebral/tests -q, plus npx jest in tray/ when tray/windows/main.html or tray/lib changed) and proceed ONLY if ALL pass. " +
             "If you launch Cerebral to smoke IPC, launch it in the BACKGROUND and ALWAYS terminate it before you finish -- leave no orphan 'python -m cerebral.main' process. " +
             "(6) Open the PR with 'Closes #N' in the body. Merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. " +
             "If gh reports the PR is not mergeable yet, wait ~15s and retry up to 5 times. If --delete-branch fails because master is checked out elsewhere, merge without it and delete the remote branch with git push origin --delete. " +
             "(7) git checkout master and git pull origin master so master is current. " +
             "(8) Rewrite the MODEL-SERVERS.md 'Next slice' block: tick the landed entry in the Queue, set the next unticked entry as Active (including its Model from the queue line -- sonnet unless the line says otherwise), " +
             "set 'Status:' (ready while slices remain; done after S3 lands, since S4 is HITL), and add the merged PR under 'Landed PRs'. " +
             "Commit the MODEL-SERVERS.md change directly to master and push it. MODEL-SERVERS.md is the ONLY thing you may commit straight to master. " +
             "(9) If tests fail and you cannot fix them, or the slice genuinely needs a human / live action, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging the PR. " +
             "(10) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Model servers loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Model servers loop" "MODEL-SERVERS.md says Status: done. All slices landed."; break }
        if ($status -eq "blocked") { Notify "Model servers loop" "MODEL-SERVERS.md says Status: blocked. A session needs your input - read MODEL-SERVERS.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in MODEL-SERVERS.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read MODEL-SERVERS.md and complete the active slice exactly as specced in its issue. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in MODEL-SERVERS.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, make sure no orphan Cerebral process is running, run the test suite, " +
                          "find and fix the problem, and finish the slice. " + $rules
            }

            $outLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.out.log" -f $runStamp, $slice, $attempt)
            $errLog = Join-Path $stateDir ("{0}-slice{1}-attempt{2}.err.log" -f $runStamp, $slice, $attempt)

            Log ("slice {0} attempt {1}/{2} starting (log: {3})" -f $slice, $attempt, $MaxAttempts, $outLog)

            # Snapshot any cerebral.main already running so we only reap Cerebrals
            # THIS attempt leaves behind, never a pre-existing one.
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
                # "Failed to authenticate" = expired/invalid stored OAuth token (401);
                # retrying is pointless, only /login fixes it.
                if ($output -match "Not logged in|Failed to authenticate") {
                    Notify "Model servers loop" "The claude CLI is not logged in (or its token expired). Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Model servers loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Model servers loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Model servers loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
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
            Notify "Model servers loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\model-servers-loop and MODEL-SERVERS.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Model servers loop ended ==="
} catch {
    # Log (not just Write-Host) so an overnight crash leaves a cause in the loop
    # log instead of a silent freeze at the Read-Host prompt.
    $err = "LOOP CRASHED: {0}`n{1}" -f $_.Exception.Message, $_.ScriptStackTrace
    Write-Host $err -ForegroundColor Red
    try { Log $err } catch {}
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
