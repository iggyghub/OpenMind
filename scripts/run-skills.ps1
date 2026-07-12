# run-skills.ps1 -- autonomous slice loop for the dev-skills campaign.
#
# Repeats: read SKILLS-BUILD.md 'Next slice' block -> launch a fresh headless Claude
# Code session (claude -p) on the recommended model -> session authors the active
# skill under .claude/skills/<name>/SKILL.md, opens a per-issue PR, MERGES it to
# master, rewrites the kickoff block -> loop.
#
# Each attempt is a brand-new session (keeps per-slice token count low);
# SKILLS-BUILD.md + the .claude/skills/write-a-skill methodology + the issue are the
# only memory between them. Logs land in .claude\tmp\skills-loop\.
#
# Stop conditions:
#   - STOP file created by scripts/stop-skills.ps1 (graceful, between steps)
#   - SKILLS-BUILD.md Status: done     (SK-5 landed)
#   - SKILLS-BUILD.md Status: blocked  (a session decided it needs a human)
#   - Claude subscription usage limit reached (auto-resume after reset)
#   - A slice fails 3 attempts in a row (debug retries exhausted)
#   - MaxSlices safety cap
# Closing this console window is the hard stop (kills the running step).
#
# SAFETY: slices author SKILL.md files only. A slice must NOT launch a real loop,
# create real issues, register a real plugin, or start a real Cerebral -- the skills
# document how to do those; building them must not do them.
#
# PREREQ (one-time): the claude CLI must be logged in. Run `claude` in a terminal,
# type /login, complete the browser flow.

param(
    [int]$MaxSlices = 5,
    [int]$MaxAttempts = 3,
    [bool]$AutoResume = $true,
    [int]$MaxLimitWaits = 6
)

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Set-Location $repoRoot

    # Raise the per-response output cap (default 32000 broke a verbose sandbox slice
    # 3x). Inherited by the child claude sessions via the process env.
    $env:CLAUDE_CODE_MAX_OUTPUT_TOKENS = "64000"

    $driver = Join-Path $repoRoot "SKILLS-BUILD.md"
    if (-not (Test-Path $driver)) { throw "SKILLS-BUILD.md not found at $driver" }

    $stateDir = Join-Path $repoRoot ".claude\tmp\skills-loop"
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
        Notify "Skills loop" "claude.cmd not found on PATH. Install Claude Code CLI (npm) first."
        exit 1
    }
    $claudeCmd = $claudeCmd.Source

    $allowedModels = @("haiku", "sonnet", "opus", "fable")
    $limitPattern  = "hit your limit|usage limit|rate limit|limit reached|out of usage|resets \d|reset at|resets at|exceeded your|approaching your|Claude usage limit|Not logged in|Failed to authenticate"

    # Skills are independent (each in its own .claude/skills/<name>/ dir), but each PR
    # still lands before the next for a clean master.
    $rules = "Hard rules, in order: " +
             "(1) Read the .claude/skills/write-a-skill methodology and SKILLS-BUILD.md first. The active slice is named in the 'Next slice' block as 'SK-x -- #N'. Run gh issue view N for its full spec. " +
             "(2) Branch off the latest origin/master (git fetch then git checkout -b skills/skN-short-name origin/master). " +
             "(3) Author ONLY that one skill: create .claude/skills/<name>/SKILL.md exactly as the issue specifies, following write-a-skill (valid frontmatter with 'name' and a trigger-rich 'description'; progressive disclosure; bundled resources ONLY if the issue calls for them). One PR per issue. " +
             "(4) SAFETY (highest priority): author SKILL.md (+ any template files the issue asks for) and NOTHING else. Do NOT launch a real loop, create real GitHub issues, register a real plugin, or start a real Cerebral -- the skill DOCUMENTS how; building it must not DO it. Any script the skill ships is a TEMPLATE, never executed by this slice. Keep all file bodies ASCII. " +
             "(5) Verify: the SKILL.md exists, its YAML frontmatter parses and has 'name' + 'description', and every bundled file the SKILL.md references actually exists. No pytest for these slices. " +
             "(6) Ground the skill in this repo's real conventions (read the files the issue names -- e.g. the four run-*.ps1 runners, docs/adr/0005, CONTEXT.md 'Plugin') so the skill is accurate, not generic. " +
             "(7) Open the PR with 'Closes #N' in the body. Merge YOUR OWN PR: gh pr merge <n> --squash --delete-branch. If gh reports the PR is not mergeable yet, wait ~15s and retry up to 5 times. " +
             "(8) git checkout master and git pull origin master so master is current. " +
             "(9) Rewrite the SKILLS-BUILD.md 'Next slice' block: tick the landed entry in the Queue, set the next unticked entry as Active (its 'SK-x -- #N' and 'Model:' line), set 'Status:' (ready while slices remain; done after SK-5 lands), and append the merged PR under 'Landed PRs'. " +
             "Commit the SKILLS-BUILD.md change directly to master and push it. SKILLS-BUILD.md is the ONLY thing you may commit straight to master -- all skill files go through the PR. " +
             "(10) If the slice genuinely needs a human decision, set Status: blocked with a one-line reason, commit that to master, and stop WITHOUT merging the PR. " +
             "(11) Leave the working tree on master with no uncommitted changes before you finish."

    Log ("=== Skills loop started (max {0} slices, {1} attempts each, auto-resume={2}) ===" -f $MaxSlices, $MaxAttempts, $AutoResume)

    $stopAll = $false
    $limitWaits = 0
    for ($slice = 1; $slice -le $MaxSlices -and -not $stopAll; $slice++) {

        if (Test-Path $stopFile) { Log "STOP file found, ending loop."; break }

        $status = Get-DriverField "Status" "ready"
        if ($status -eq "done")    { Notify "Skills loop" "SKILLS-BUILD.md says Status: done. All slices landed."; break }
        if ($status -eq "blocked") { Notify "Skills loop" "SKILLS-BUILD.md says Status: blocked. A session needs your input - read SKILLS-BUILD.md."; break }

        $model = Get-DriverField "Model" "sonnet"
        if ($allowedModels -notcontains $model) {
            Log ("Invalid Model: '{0}' in SKILLS-BUILD.md, falling back to sonnet" -f $model)
            $model = "sonnet"
        }

        Log ("--- slice {0}: model={1} ---" -f $slice, $model)

        $succeeded = $false
        for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {

            if (Test-Path $stopFile) { Log "STOP file found, ending loop."; $stopAll = $true; break }

            if ($attempt -eq 1) {
                $prompt = "Read SKILLS-BUILD.md and author the active skill exactly as specced in its issue, following the .claude/skills/write-a-skill methodology. " + $rules
            } else {
                $prompt = ("This is debug attempt {0} of {1} for the active slice in SKILLS-BUILD.md. " -f $attempt, $MaxAttempts) +
                          "A previous attempt failed or exited with an error. Inspect git status and recent " +
                          "changes, make sure the SKILL.md frontmatter parses, " +
                          "find and fix the problem, and finish the slice. " + $rules
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
                # "Failed to authenticate" = expired/invalid stored OAuth token (401);
                # retrying is pointless, only /login fixes it.
                if ($output -match "Not logged in|Failed to authenticate") {
                    Notify "Skills loop" "The claude CLI is not logged in (or its token expired). Open a terminal, run claude, type /login, then restart the loop."
                    $stopAll = $true
                    break
                }

                $resetTxt = ""
                if ($output -match "resets[^\r\n]*") { $resetTxt = $matches[0].Trim() }

                if (-not $AutoResume) {
                    $msg = "Claude usage limit reached. Loop stopped cleanly - restart after reset."
                    if ($resetTxt) { $msg = "Claude usage limit reached ({0}). Loop stopped cleanly - restart after reset." -f $resetTxt }
                    Notify "Skills loop" $msg
                    $stopAll = $true
                    break
                }

                $limitWaits++
                if ($limitWaits -gt $MaxLimitWaits) {
                    Notify "Skills loop" ("Usage limit hit {0} times in a row - stopping to avoid an endless wait. Check your plan, then restart." -f $MaxLimitWaits)
                    $stopAll = $true
                    break
                }

                $waitSec = Get-SecondsUntilReset $output
                $resumeAt = (Get-Date).AddSeconds($waitSec).ToString("h:mm tt")
                Log ("slice {0}: usage limit hit ({1}); sleeping {2}s, auto-resume at ~{3} (wait {4}/{5})" -f `
                    $slice, $resetTxt, $waitSec, $resumeAt, $limitWaits, $MaxLimitWaits)
                NotifyTimed "Skills loop" ("Usage limit reached{0}. Sleeping until ~{1}, then auto-resuming. Closing this window cancels." -f `
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
            Notify "Skills loop" ("Slice failed after {0} attempts. Check the logs in .claude\tmp\skills-loop and SKILLS-BUILD.md, then restart the loop." -f $MaxAttempts)
            break
        }
    }

    Log "=== Skills loop ended ==="
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
