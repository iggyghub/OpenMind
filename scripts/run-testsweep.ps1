# run-testsweep.ps1 -- autonomous batch loop for the Test Sweep campaign.
#
# Repeats: read TEST-SWEEP.md 'Next slice' block -> launch a fresh headless
# Claude Code session (claude -p) on the recommended model -> session runs the
# active batch's test command, records the results in TEST-SWEEP.md, ticks the
# batch, advances the Active pointer -> loop.
#
# Each attempt is a brand-new session, so every batch starts with a fresh
# context window; TEST-SWEEP.md is the only memory between them. A usage-limit
# hit sleeps until the token reset, then resumes the same batch.
# Logs land in .claude\tmp\testsweep-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-testsweep.ps1 (graceful, between steps)
#   - TEST-SWEEP.md Status: done    (all batches recorded)
#   - TEST-SWEEP.md Status: blocked (a session decided it needs a human)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A batch fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: sessions run tests and edit TEST-SWEEP.md only -- no code changes,
# no PRs, no live services, no fixing failures. Enforced in the prompt rules
# below and in TEST-SWEEP.md "SAFETY".
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a
# terminal, type /login, complete the browser flow.

param(
    [int]$MaxSlices = 10,
    [int]$MaxAttempts = 3,
    [bool]$AutoResume = $true,
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "64000"

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    $driver = Join-Path $repoRoot "TEST-SWEEP.md"
    if (-not (Test-Path $driver)) { throw "TEST-SWEEP.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\testsweep-loop"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $stopFile = Join-Path $stateDir "STOP"
    if (Test-Path $stopFile) { Remove-Item $stopFile -Force }

    $runStamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $loopLog  = Join-Path $stateDir ("loop-{0}.log" -f $runStamp)

    function Log($msg) {
        $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg
        Write-Host $line
        # Best-effort: a reader holding the log (tail -f, an editor) must never
        # kill the loop with a share-violation on append.
        try { Add-Content -LiteralPath $loopLog -Value $line -ErrorAction Stop } catch {}
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
        $m = Select-String -LiteralPath $driver `
            -Pattern ("^{0}:\s*(\S+)" -f $name) | Select-Object -First 1
        if ($null -eq $m) { return $default }
        return $m.Matches[0].Groups[1].Value.ToLower()
    }

    $claudeCmd = Get-Command claude.cmd -ErrorAction SilentlyContinue
    if ($null -eq $claudeCmd) {
        Notify "Test Sweep loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in|Failed to authenticate"

    $rules = "Hard rules, in order: " +
             "(1) Read TEST-SWEEP.md first. The active batch is named in the 'Next slice' block as 'Bx'. Its full spec is its command in the 'Batch commands' section of TEST-SWEEP.md -- there is no GitHub issue for it. " +
             "(2) Make sure you are on master with a clean tree: git checkout master, git pull origin master. " +
             "(3) Run ONLY the active batch's command exactly as written, from the repo root. Do NOT launch Cerebral or any live service, do NOT fetch anything live, do NOT modify any code -- this campaign only runs tests and records results. " +
             "(4) Record the outcome under '## Results' in TEST-SWEEP.md using the format documented there: PASS with counts and duration, or FAIL with counts plus one indented line per failing test id and its one-line error. Never truncate the failure list. Integration-marked tests that skip without live services stay skipped -- count them as skips, not failures. " +
             "(5) Do NOT fix failing tests, do NOT open PRs, do NOT file issues -- failures are triaged by a human after the sweep. A batch with failures still counts as finished once its results are recorded. " +
             "(6) Rewrite the 'Next slice' block: tick the finished batch in the queue, set the next unticked batch as Active (and its Model: line), and set Status: (ready while batches remain; done after B8 is recorded). " +
             "(7) Commit the TEST-SWEEP.md change directly to master and push it. TEST-SWEEP.md is the ONLY file you may commit -- never commit code, test artifacts, or logs; leave any stray generated files untouched. " +
             "(8) If the batch command itself cannot run at all (pytest or jest missing, import error kills collection), record what happened under Results, set Status: blocked with a one-line reason, commit that, and stop. " +
             "(9) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Test Sweep loop started (max {0} batches, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Test Sweep loop" "TEST-SWEEP.md says Status: done. All batches recorded."; break }
        if ($status -eq "blocked") { Notify "Test Sweep loop" "TEST-SWEEP.md says Status: blocked. A session needs your input - read TEST-SWEEP.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in TEST-SWEEP.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- batch {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read TEST-SWEEP.md and run the active batch exactly as specced there. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active batch in TEST-SWEEP.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and the " +
                          "Results section, revert any non-driver file changes, re-run the batch, record " +
                          "the results, and finish the batch. " + $rules
            }

            $outLog = Join-Path $stateDir ("{0}-batch{1}-attempt{2}.out.log" -f $runStamp, $slice, $attempt)
            $errLog = Join-Path $stateDir ("{0}-batch{1}-attempt{2}.err.log" -f $runStamp, $slice, $attempt)

            Log ("batch {0} attempt {1}/{2} starting (log: {3})" -f $slice, $attempt, $MaxAttempts, $outLog)

            # Snapshot any cerebral.main already running so we only reap Cerebrals
            # THIS attempt leaves behind, never a pre-existing one.
            $pyBefore = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -like '*cerebral.main*' } |
                Select-Object -ExpandProperty ProcessId)

            $argString = '-p --model {0} --dangerously-skip-permissions "{1}"' -f $model, $prompt
            # Launch detached (not -Wait): a session that leaves a child process running
            # would inherit the redirected stdout handle and wedge -Wait on stream EOF.
            $proc = Start-Process -FilePath $claudeCmd -ArgumentList $argString `
                -WorkingDirectory $repoRoot -NoNewWindow -PassThru `
                -RedirectStandardOutput $outLog -RedirectStandardError $errLog
            # PS 5.1 quirk: touch .Handle once so .ExitCode is populated after exit.
            $null = $proc.Handle

            $maxRunSec = 5400
            $polled = 0
            while (-not $proc.HasExited) {
                if (Test-Path $stopFile) {
                    Log ("batch {0}: STOP file found, killing the running session" -f $slice)
                    try { $proc.Kill() } catch {}
                    break
                }
                if ($polled -ge $maxRunSec) {
                    Log ("batch {0}: attempt exceeded {1}s, killing the session" -f $slice, $maxRunSec)
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
                    Log ("batch {0}: reaping orphan Cerebral pid {1}" -f $slice, $_.ProcessId)
                    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
                }

            $output = ""
            if (Test-Path $outLog) { $output += [IO.File]::ReadAllText($outLog) }
            if (Test-Path $errLog) { $output += [IO.File]::ReadAllText($errLog) }

            if ($output -match $limitPattern) {
                # "Failed to authenticate" = expired/invalid stored OAuth token (401);
                # retrying is pointless, only /login fixes it.
                if ($output -match "Not logged in|Failed to authenticate") {
                    Notify "Test Sweep loop" "The claude CLI is not logged in (or its token expired). Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Test Sweep loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Test Sweep loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("batch {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Test Sweep loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
                    $(if ($resetTxt) { " ($resetTxt)" } else { "" }), $resumeAt) 30

                $sleptFull = Wait-WithStopCheck $waitSec $stopFile
                if (-not $sleptFull) { Log "STOP file found during limit wait, ending loop."; $stopAll = $true; break }

                Log ("batch {0}: resuming after limit wait, retrying attempt {1}" -f $slice, $attempt)
                $attempt--
                continue
            }

            $limitWaits = 0

            $exitCode = $null
            try { $exitCode = $proc.ExitCode } catch { $exitCode = $null }

            if ($exitCode -eq 0) {
                Log ("batch {0} attempt {1} succeeded" -f $slice, $attempt)
                $succeeded = $true
                break
            }

            Log ("batch {0} attempt {1} FAILED (exit {2})" -f $slice, $attempt, $(if ($null -eq $exitCode) { "unknown" } else { $exitCode }))
        }

        if ($stopAll) { break }

        if (-not $succeeded) {
            Notify "Test Sweep loop" ("Batch failed after {0} attempts. Check the logs in .claude\tmp\testsweep-loop and TEST-SWEEP.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Test Sweep loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
