# setup-felix-account.ps1 -- one-click provisioning for Felix's isolated session.
#
# Creates/repairs everything the second-session vehicle needs, in ONE elevated
# pass so you never type PowerShell by hand (ADR-0016 #601/#604):
#   1. the dedicated STANDARD (non-admin) Windows user "Felix"
#   2. membership in "Remote Desktop Users"
#   3. Remote Desktop enabled + its firewall rule
#
# These are admin/system-security actions, so Windows requires ONE UAC approval:
# the script self-elevates (a single "Yes" click), then does the rest silently.
# Idempotent -- safe to re-run; it skips whatever is already in place.
#
# Password: reused from what you saved in the Felix app (Credentials ->
# "Felix session account") / scripts/set-felix-session-login.ps1, keyring
# service openmind-felix-session / user Felix. If none is stored AND the user
# must be created, it prompts once and stores it there.
#
# Felix (Cerebral) can launch this itself when provisioning is needed -- the UAC
# prompt is the human's one-click consent. Until #604 wires that, double-click it.

param(
    [string]$User = "Felix",
    [bool]$EnableRdp = $true
)

# ---- self-elevate (the one UAC click) --------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Host "Requesting administrator approval (one UAC prompt)..." -ForegroundColor Cyan
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-User", $User, "-EnableRdp", $EnableRdp
    )
    exit
}

try {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    Write-Host "=== Felix isolated-session setup (elevated) ===" -ForegroundColor Cyan
    Write-Host ""

    $svc = "openmind-felix-session"

    # ---- 1. user exists? create as STANDARD (non-admin) ----------------------
    $exists = $null -ne (Get-LocalUser -Name $User -ErrorAction SilentlyContinue)
    if ($exists) {
        Write-Host "[ok] user '$User' already exists (leaving its password as-is)" -ForegroundColor Green
    } else {
        # Need a password to create it -- reuse the stored one, else prompt+store.
        $pw = & python -c "import keyring;print(keyring.get_password('$svc','$User') or '')" 2>$null
        if ([string]::IsNullOrEmpty($pw)) {
            Write-Host "No stored password found; set one for the new '$User' account:" -ForegroundColor Yellow
            $sec = Read-Host "Password for '$User'" -AsSecureString
            $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
            $pw = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
            & python -c "import keyring,sys;keyring.set_password('$svc','$User',sys.argv[1])" "$pw" | Out-Null
            Write-Host "[ok] password stored in Credential Manager ($svc/$User)" -ForegroundColor Green
        }
        $secure = ConvertTo-SecureString $pw -AsPlainText -Force
        $pw = $null
        New-LocalUser -Name $User -Password $secure -FullName "OpenMind Felix" `
            -Description "Isolated second session for Felix (ADR-0016)" `
            -PasswordNeverExpires -AccountNeverExpires | Out-Null
        Add-LocalGroupMember -Group "Users" -Member $User -ErrorAction SilentlyContinue
        Write-Host "[ok] created standard user '$User'" -ForegroundColor Green
    }

    # ---- 2. Remote Desktop Users membership ---------------------------------
    $inRdp = Get-LocalGroupMember -Group "Remote Desktop Users" -ErrorAction SilentlyContinue |
             Where-Object { $_.Name -like "*\$User" }
    if ($inRdp) {
        Write-Host "[ok] '$User' already in Remote Desktop Users" -ForegroundColor Green
    } else {
        Add-LocalGroupMember -Group "Remote Desktop Users" -Member $User
        Write-Host "[ok] added '$User' to Remote Desktop Users" -ForegroundColor Green
    }

    # ---- 3. enable Remote Desktop + firewall --------------------------------
    if ($EnableRdp) {
        Set-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" `
            -Name fDenyTSConnections -Value 0
        Enable-NetFirewallRule -DisplayGroup "Remote Desktop" -ErrorAction SilentlyContinue
        Write-Host "[ok] Remote Desktop enabled (+ firewall rule)" -ForegroundColor Green
    }

    # ---- guardrail: never let Felix be an admin -----------------------------
    $isAdmin = Get-LocalGroupMember -Group "Administrators" -ErrorAction SilentlyContinue |
               Where-Object { $_.Name -like "*\$User" }
    if ($isAdmin) {
        Write-Host "[warn] '$User' is an Administrator -- removing (must stay standard)." -ForegroundColor Yellow
        Remove-LocalGroupMember -Group "Administrators" -Member $User -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "SUCCESS: '$User' is provisioned for the isolated session." -ForegroundColor Green
    Write-Host "Next: scripts\spike-second-session.ps1 to bring the second session up." -ForegroundColor Green
    exit 0
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    exit 1
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
