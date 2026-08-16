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

# 1) Remove "Device Descriptor Request Failed" ghosts. Until these are gone Windows
#    will not re-enumerate the board as VID_1209/PID_5740 on the next plug.
$failed = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.FriendlyName -like '*Descriptor Request Failed*' })

if ($failed.Count -eq 0) {
    Write-Host 'No failed USB descriptors present.'
} else {
    foreach ($device in $failed) {
        Write-Host "Removing failed device: $($device.InstanceId)"
        try {
            Disable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
        } catch {}
        Start-Sleep -Milliseconds 500
        pnputil /remove-device "$($device.InstanceId)" 2>&1 | Out-Host
    }
    Write-Host 'Rescanning PnP...'
    pnputil /scan-devices 2>&1 | Out-Host
    Start-Sleep -Seconds 3
}

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
if (-not $stuck) { $stuck = '2-5' }

Write-Host "No healthy device yet. Sharing busid $stuck for replug auto-attach..."
& $Usbipd bind --busid $stuck 2>&1 | Out-Host
& $Usbipd list
Write-Host ''
Write-Host 'ACTION REQUIRED: unplug the USB cable, then plug it into the SAME port.'
Write-Host 'The caller will auto-attach once Windows re-enumerates the board.'
exit 3
