# Clear a stuck flight controller USB enumeration on Windows, then share it with WSL.
#
# Run elevated by Vector/tools/wsl-usb.sh via UAC. Removing a ghost PnP node needs
# Administrator, which is the only reason this is a separate elevated script.
#
# Exit codes:  0 attached   3 replug required   other = usbipd failure
$ErrorActionPreference = 'Continue'

$Usbipd = 'C:\Program Files\usbipd-win\usbipd.exe'
if (-not (Test-Path $Usbipd)) {
    $Usbipd = 'C:\Program Files (x86)\usbipd-win\usbipd.exe'
}
if (-not (Test-Path $Usbipd)) {
    Write-Host 'usbipd-win is not installed: https://github.com/dorssel/usbipd-win'
    exit 4
}

function Get-UsbipdState {
    & $Usbipd state | ConvertFrom-Json
}

Write-Host '=== Vector USB reset ==='

function Get-DeviceParent([string]$InstanceId) {
    $prop = Get-PnpDeviceProperty -InstanceId $InstanceId -KeyName 'DEVPKEY_Device_Parent' -ErrorAction SilentlyContinue
    if ($prop) { return [string]$prop.Data }
    return $null
}

function Cycle-PnpDevice([string]$InstanceId, [string]$Label) {
    if (-not $InstanceId) { return }
    Write-Host "Cycling $Label : $InstanceId"
    try { Disable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop } catch {}
    Start-Sleep -Seconds 2
    try { Enable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop } catch {}
}

# Drop usbipd's persisted "Descriptor Request Failed" bind. That ghost keeps the
# next plug from coming up as ArduPilot on a free busid.
$state = Get-UsbipdState
foreach ($device in $state.Devices) {
    $description = [string]$device.Description
    $guid = [string]$device.PersistedGuid
    if (-not $guid) { continue }
    if ($description -like '*Descriptor Request Failed*') {
        Write-Host "Unbinding persisted failed descriptor $guid"
        & $Usbipd unbind --guid $guid 2>&1 | Out-Host
    }
}

# 1) Remove "Device Descriptor Request Failed" ghosts. Until these are gone Windows
#    will not re-enumerate the board as VID_1209/PID_5740 on the next plug.
$failed = @(Get-PnpDevice -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -like '*Descriptor Request Failed*' })

$parents = New-Object System.Collections.Generic.HashSet[string]
if ($failed.Count -eq 0) {
    Write-Host 'No failed USB descriptors present.'
} else {
    foreach ($device in $failed) {
        $parent = Get-DeviceParent $device.InstanceId
        if ($parent) { [void]$parents.Add($parent) }
        Write-Host "Removing failed device: $($device.InstanceId)"
        try {
            Disable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
        } catch {}
        Start-Sleep -Milliseconds 500
        pnputil /remove-device "$($device.InstanceId)" 2>&1 | Out-Host
    }
}

# Software unplug: bounce the hub port the failed device was on. That is what
# a cable yank does, without waiting on a physical replug.
foreach ($parent in $parents) {
    Cycle-PnpDevice $parent 'USB hub'
}

Write-Host 'Rescanning PnP...'
pnputil /scan-devices 2>&1 | Out-Host
Start-Sleep -Seconds 3

# 2) If the board is healthy now, share and attach it.
$state = Get-UsbipdState
$target = $null
foreach ($device in $state.Devices) {
    if ($null -eq $device.BusId) { continue }
    $description = [string]$device.Description
    $instance = [string]$device.InstanceId
    if ($description -like '*Descriptor Request Failed*') { continue }
    if ($description -match 'ArduPilot|Matek' -or
        $instance -match 'VID_1209&PID_5740' -or
        $instance -match 'VID_2DAE&') {
        $target = $device.BusId
        Write-Host "Matched flight controller on busid $target ($description)"
        break
    }
}

if ($target) {
    Write-Host "Binding busid $target..."
    & $Usbipd bind --busid $target
    Write-Host "Attaching busid $target to WSL..."
    & $Usbipd attach --wsl --busid $target
    & $Usbipd list
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'USB reset complete.'
        exit 0
    }
    exit $LASTEXITCODE
}

# 3) Nothing healthy. Share the stuck bus so the caller can arm auto-attach and wait
#    for a cable replug.
$stuck = $null
foreach ($device in $state.Devices) {
    if ($null -eq $device.BusId) { continue }
    if ([string]$device.Description -like '*Descriptor Request Failed*') {
        $stuck = $device.BusId
        break
    }
}
if (-not $stuck) {
    Write-Host 'No healthy device and no stuck busid to share. Unplug and replug the cable.'
    & $Usbipd list
    exit 3
}

Write-Host "No healthy device yet. Sharing busid $stuck for replug auto-attach..."
& $Usbipd bind --busid $stuck 2>&1 | Out-Host
& $Usbipd list
Write-Host ''
Write-Host 'ACTION REQUIRED: unplug the USB cable, then plug it into the SAME port.'
Write-Host 'The caller will auto-attach once Windows re-enumerates the board.'
exit 3
