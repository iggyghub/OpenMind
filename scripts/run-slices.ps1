# run-slices.ps1 -- autonomous slice loop for OpenMind development.
#
# Repeats: read HANDOFF.md kickoff block -> launch a fresh headless Claude
# Code session (claude -p) on the recommended model -> session completes the
# slice, ends at an OPEN (unmerged) PR, and rewrites the kickoff block for
# the next slice -> loop.
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
    [int]$MaxSlices = 8,
    [int]$MaxAttempts = 3
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
        # 48 = exclamation icon; 0 = wait until dismissed
        $shell.Popup($msg, 0, $title, 48) | Out-Null
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

    $rules = "Hard rules: one PR per issue; run the relevant tests before opening the PR; " +
             "open the PR with Closes #N in the body; do NOT merge any PR; do NOT push to master directly; " +
             "finish by rewriting the HANDOFF.md kickoff block for the slice after this one, " +
             "including the Model: line (haiku, sonnet, opus, or fable) and the Status: line " +
             "(ready, blocked, or done). If you cannot complete the slice, set Status: blocked " +
             "with a one-line reason and stop."

    Log ("=== slice loop started (max {0} slices, {1} attempts each) ===" -f $MaxSlices, $MaxAttempts)

    $stopAll = $false
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
                if ($output -match "Not logged in") {
                    Notify "Slice loop" "The claude CLI is not logged in. Open a terminal, run claude, type /login, then restart the loop."
                } else {
                    $resetMsg = "Claude usage limit reached. Loop stopped cleanly - restart it after the limit resets."
                    if ($output -match "resets[^\r\n]*") { $resetMsg = "Claude usage limit reached (" + $matches[0].Trim() + "). Loop stopped cleanly - restart after reset." }
                    Notify "Slice loop" $resetMsg
                }
                $stopAll = $true
                break
            }

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
