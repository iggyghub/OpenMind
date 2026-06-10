# sync-slice-model.ps1 -- sync the Claude Code default model from HANDOFF.md
# into .claude/settings.local.json.
#
# Convention: the session that finishes a slice updates the kickoff block in
# HANDOFF.md, including a line of the form
#
#   Model: sonnet
#
# (allowed: haiku | sonnet | opus | fable). This script copies that value into
# the "model" key of .claude/settings.local.json, which Claude Code (CLI and
# desktop app) reads at session start. Run automatically by the SessionEnd
# hook configured in the same file, so the NEXT session in this project opens
# on the model the previous session recommended.
#
# Chained/hook script: no Read-Host pause, clean exit codes only.
# Exit 0 on success or benign skip (no Model line); exit 1 on real failure.

$ErrorActionPreference = "Stop"

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $handoff  = Join-Path $repoRoot "HANDOFF.md"
    $settings = Join-Path $repoRoot ".claude\settings.local.json"

    if (-not (Test-Path $handoff))  { Write-Output "sync-slice-model: no HANDOFF.md, skipping"; exit 0 }
    if (-not (Test-Path $settings)) { Write-Output "sync-slice-model: no settings.local.json, skipping"; exit 0 }

    $allowed = @("haiku", "sonnet", "opus", "fable")
    $match = Select-String -LiteralPath $handoff -Pattern '^Model:\s*(\S+)' |
        Select-Object -First 1
    if ($null -eq $match) { Write-Output "sync-slice-model: no Model: line in HANDOFF.md, skipping"; exit 0 }

    $model = $match.Matches[0].Groups[1].Value.ToLower()
    if ($allowed -notcontains $model) {
        Write-Output ("sync-slice-model: 'Model: {0}' not in ({1}), skipping" -f $model, ($allowed -join ", "))
        exit 0
    }

    $json = Get-Content -LiteralPath $settings -Raw | ConvertFrom-Json
    if ($json.PSObject.Properties.Name -contains "model" -and $json.model -eq $model) {
        Write-Output ("sync-slice-model: already {0}, nothing to do" -f $model)
        exit 0
    }

    $json | Add-Member -NotePropertyName "model" -NotePropertyValue $model -Force
    # Depth must cover nested hooks config; default depth 2 would mangle it.
    $json | ConvertTo-Json -Depth 16 | Set-Content -LiteralPath $settings -Encoding utf8
    Write-Output ("sync-slice-model: set model = {0}" -f $model)
    exit 0
} catch {
    Write-Output ("sync-slice-model FAILED: {0}" -f $_.Exception.Message)
    exit 1
}
