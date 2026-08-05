# setup-felix-vm.ps1 -- host-side setup for Felix's VM guest (ADR-0016 #601, option D).
#
# The isolated-session vehicle is a VM (client Windows can't host a concurrent
# ACTIVE second session -- see the 2026-08-04 ADR amendment). This automates the
# HOST side; the guest OS install + license are yours.
#
# Two phases (self-elevating -- one UAC each):
#   Phase 1 (no -IsoPath): enable Hyper-V, then REBOOT.
#   Phase 2 (-IsoPath given, after reboot): create + start the "FelixVM" guest,
#           boot the installer, and open the VM window so you install Windows.
#
# After Windows is installed in the guest, run the guest worker bootstrap inside
# it (docs/computer-use-live-verify.md) so session_worker connects back to host
# Cerebral over the VM network.
#
# Idempotent: skips Hyper-V if already on and the VM if it already exists.

param(
    [string]$Name = "FelixVM",
    [int]$MemoryGB = 4,
    [int]$DiskGB = 60,
    [string]$IsoPath = "",
    [string]$VmPath = ""   # where the VM + disk live; defaults to D:\FelixVM if D: exists
)

# ---- self-elevate (one UAC) -------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    Write-Host "Requesting administrator approval (one UAC prompt)..." -ForegroundColor Cyan
    Start-Process powershell -Verb RunAs -ArgumentList @(
        "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"",
        "-Name", $Name, "-MemoryGB", $MemoryGB, "-DiskGB", $DiskGB,
        "-IsoPath", "`"$IsoPath`"", "-VmPath", "`"$VmPath`""
    )
    exit
}

try {
    Write-Host "=== Felix VM host setup (elevated) ===" -ForegroundColor Cyan
    Write-Host ""

    # ---- Phase 1: Hyper-V ---------------------------------------------------
    $hv = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -ErrorAction SilentlyContinue
    if ($null -eq $hv) {
        throw "This edition has no Hyper-V feature. Win10 Pro/Enterprise required (yours is Pro)."
    }
    if ($hv.State -ne "Enabled") {
        Write-Host "Enabling Hyper-V (this needs a reboot)..." -ForegroundColor Yellow
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All -NoRestart | Out-Null
        Write-Host ""
        Write-Host "SUCCESS (phase 1): Hyper-V enabled." -ForegroundColor Green
        Write-Host "REBOOT now, then re-run this with an installer ISO:" -ForegroundColor Green
        Write-Host "  scripts\setup-felix-vm.ps1 -IsoPath 'C:\path\to\Windows.iso'" -ForegroundColor Green
        exit 0
    }
    Write-Host "[ok] Hyper-V is enabled" -ForegroundColor Green

    # ---- Phase 2: the VM ----------------------------------------------------
    if ([string]::IsNullOrWhiteSpace($IsoPath)) {
        Write-Host ""
        Write-Host "Hyper-V is ready. Re-run with an installer ISO to create the guest:" -ForegroundColor Yellow
        Write-Host "  scripts\setup-felix-vm.ps1 -IsoPath 'C:\path\to\Windows.iso'" -ForegroundColor Yellow
        Write-Host "(A Windows guest OS + license is yours to provide -- I can't install it.)"
        exit 0
    }
    if (-not (Test-Path -LiteralPath $IsoPath)) { throw "ISO not found: $IsoPath" }

    if (Get-VM -Name $Name -ErrorAction SilentlyContinue) {
        Write-Host "[ok] VM '$Name' already exists -- starting it" -ForegroundColor Green
    } else {
        if ([string]::IsNullOrWhiteSpace($VmPath)) {
            $VmPath = if (Test-Path "D:\") { "D:\FelixVM" } else { Join-Path $env:PUBLIC "FelixVM" }
        }
        New-Item -ItemType Directory -Force -Path $VmPath | Out-Null
        $vhd = Join-Path $VmPath "$Name.vhdx"
        Write-Host "VM location: $VmPath" -ForegroundColor Cyan

        Write-Host "Creating VM '$Name' ($MemoryGB GB RAM, $DiskGB GB disk)..." -ForegroundColor Cyan
        New-VM -Name $Name -Generation 2 -MemoryStartupBytes ($MemoryGB * 1GB) `
            -Path $VmPath -NewVHDPath $vhd -NewVHDSizeBytes ($DiskGB * 1GB) `
            -SwitchName "Default Switch" | Out-Null
        Set-VM -Name $Name -DynamicMemory -MemoryMinimumBytes 2GB -MemoryMaximumBytes ($MemoryGB * 1GB)
        Set-VMProcessor -VMName $Name -Count 2
        Add-VMDvdDrive -VMName $Name -Path $IsoPath
        # Boot from the DVD first, and keep integration services on for later
        # host<->guest coordination (the worker still talks over the network).
        $dvd = Get-VMDvdDrive -VMName $Name
        Set-VMFirmware -VMName $Name -FirstBootDevice $dvd
        Enable-VMIntegrationService -VMName $Name -Name "Guest Service Interface"
        Write-Host "[ok] VM '$Name' created" -ForegroundColor Green
    }

    Start-VM -Name $Name
    Start-Process vmconnect.exe -ArgumentList "localhost", $Name -ErrorAction SilentlyContinue

    Write-Host ""
    Write-Host "SUCCESS: '$Name' is running -- the VM window is open." -ForegroundColor Green
    Write-Host "Install Windows in it, then run the guest worker bootstrap (see" -ForegroundColor Green
    Write-Host "docs\computer-use-live-verify.md) so session_worker connects to host Cerebral." -ForegroundColor Green
    exit 0
} catch {
    Write-Host ("FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray
    exit 1
} finally {
    Read-Host "Press Enter to close" | Out-Null
}
