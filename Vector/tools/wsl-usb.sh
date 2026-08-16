#!/usr/bin/env bash
# Attach the flight controller's USB serial port to WSL.
#
# WSL does not see USB devices until usbipd-win shares and attaches them, and the
# H743's CDC interface sometimes enumerates as "Device Descriptor Request Failed",
# which needs a ghost-node removal and a physical replug to clear. This script walks
# that recovery so the dashboard can just be started.
#
#   Vector/tools/wsl-usb.sh
#   USB_MATCH='VID:PID' Vector/tools/wsl-usb.sh
#
# On a native Linux host this is unnecessary: plug the board in and the port appears.
set -euo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATTACH_TIMEOUT_S="${ATTACH_TIMEOUT_S:-30}"

# ArduPilot's CDC VID:PID, plus Matek's vendor ID and the description strings.
USB_MATCH="${USB_MATCH:-ArduPilot|Matek|1209:5740|2DAE:}"

# The port this bench normally uses, as a fallback when the ghost node has already
# been removed and there is nothing left to identify.
FALLBACK_BUSID="${FALLBACK_BUSID:-2-5}"

find_usbipd() {
  if command -v usbipd.exe >/dev/null 2>&1; then
    command -v usbipd.exe
    return 0
  fi
  local path
  for path in "/mnt/c/Program Files/usbipd-win/usbipd.exe" \
    "/mnt/c/Program Files (x86)/usbipd-win/usbipd.exe"; do
    if [[ -x "$path" ]]; then
      printf '%s\n' "$path"
      return 0
    fi
  done
  return 1
}

serial_ports() {
  local path found=()
  for path in /dev/ttyACM* /dev/ttyUSB*; do
    [[ -e "$path" ]] && found+=("$path")
  done
  ((${#found[@]})) && printf '%s\n' "${found[*]}"
}

have_serial() { [[ -n "$(serial_ports)" ]]; }

usbipd_state() {
  "$1" state 2>/dev/null || echo '{"Devices":[]}'
}

# Print "BUSID<TAB>healthy|failed" for the best candidate, or nothing.
find_fc() {
  usbipd_state "$1" | python3 -c '
import json, re, sys

data = json.load(sys.stdin)
pattern = re.compile(sys.argv[1], re.I)
healthy = failed = None

for device in data.get("Devices", []):
    bus = device.get("BusId")
    if not bus:
        continue
    description = device.get("Description") or ""
    instance = (device.get("InstanceId") or "").upper()
    match = re.search(r"VID_([0-9A-F]+)&PID_([0-9A-F]+)", instance)
    vidpid = f"{match.group(1)}:{match.group(2)}" if match else ""

    # A stuck enumeration reports a failed descriptor and a null VID:PID.
    if "Descriptor Request Failed" in description or vidpid in ("0000:0002", "0000:0000"):
        failed = failed or bus
        continue
    if pattern.search(f"{description} {vidpid} {instance}"):
        healthy = bus
        break

pick = healthy or failed
if pick:
    print(f"{pick}\t{'"'"'healthy'"'"' if healthy else '"'"'failed'"'"'}")
' "$USB_MATCH"
}

# True when usbipd remembers the board, meaning it is plugged in but not enumerated.
persisted_fc() {
  usbipd_state "$1" | python3 -c '
import json, sys

data = json.load(sys.stdin)
for device in data.get("Devices", []):
    description = device.get("Description") or ""
    instance = (device.get("InstanceId") or "").upper()
    if device.get("PersistedGuid") and ("ArduPilot" in description or "VID_1209&PID_5740" in instance):
        sys.exit(0)
sys.exit(1)
'
}

bus_state() {
  "$1" list 2>/dev/null | awk -v bus="$2" '
    $1 == bus {
      if ($0 ~ /[[:space:]]Attached[[:space:]]*$/) { print "Attached"; exit }
      if ($0 ~ /Not shared/) { print "Not shared"; exit }
      if ($0 ~ /Shared/)     { print "Shared";     exit }
      print "Unknown"; exit
    }
  '
}

wait_for_serial() {
  local timeout_s="${1:-$ATTACH_TIMEOUT_S}" i
  printf 'Waiting for serial device'
  for ((i = 0; i < timeout_s; i++)); do
    if have_serial; then
      printf '\nUSB ready: %s\n' "$(serial_ports)"
      return 0
    fi
    printf '.'
    sleep 1
  done
  printf '\n'
  return 1
}

# Removing a ghost PnP node needs Administrator, so this runs the helper elevated
# via UAC. Paths are resolved inside PowerShell so the shell never has to quote
# Windows path separators.
run_reset_elevated() {
  local script="$TOOLS/usb-reset.ps1"
  echo "Requesting Administrator rights to reset the USB port (expect a UAC prompt)..."
  powershell.exe -NoProfile -Command '& {
    $ErrorActionPreference = "Stop"
    $src = (wsl.exe -e wslpath -w "'"$script"'").Trim()
    $dst = Join-Path $env:TEMP "vector_usb_reset.ps1"
    Copy-Item -LiteralPath $src -Destination $dst -Force
    $proc = Start-Process -FilePath powershell.exe `
      -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",$dst) `
      -Verb RunAs -PassThru -Wait
    exit $proc.ExitCode
  }'
}

attach_bus() {
  local usbipd="$1" bus="$2" state
  state="$(bus_state "$usbipd" "$bus")"
  state="${state:-missing}"
  echo "Found busid=$bus state=$state"

  if [[ "$state" == "Not shared" || "$state" == "Unknown" || "$state" == "missing" ]]; then
    echo "Sharing busid $bus..."
    if ! "$usbipd" bind --busid "$bus" 2>/dev/null; then
      run_reset_elevated || true
      state="$(bus_state "$usbipd" "$bus")"
      if [[ "$state" == "Not shared" || -z "$state" ]]; then
        echo "Still not shared. From an elevated PowerShell:"
        echo "  usbipd bind --busid $bus"
        echo "  usbipd attach --wsl --busid $bus"
        return 1
      fi
    fi
  fi

  if [[ "$(bus_state "$usbipd" "$bus")" != "Attached" ]]; then
    echo "Attaching busid $bus to WSL..."
    if ! "$usbipd" attach --wsl --busid "$bus"; then
      run_reset_elevated || true
      "$usbipd" attach --wsl --busid "$bus" 2>/dev/null || {
        echo "Attach failed. From PowerShell:  usbipd attach --wsl --busid $bus"
        return 1
      }
    fi
  fi
}

# Arm auto-attach, then ask for the physical replug that clears a stuck enumeration.
await_replug() {
  local usbipd="$1" bus="$2" watcher
  cat <<EOF

USB port $bus needs a physical replug.
  1. Unplug the flight controller's USB cable
  2. Plug it back into the same port
Auto-attach is armed for ${ATTACH_TIMEOUT_S}s...
EOF

  "$usbipd" bind --busid "$bus" 2>/dev/null || run_reset_elevated || true
  "$usbipd" attach --wsl --busid "$bus" --auto-attach --unplugged \
    >/tmp/vector-autoattach.log 2>&1 &
  watcher=$!

  if wait_for_serial "$ATTACH_TIMEOUT_S"; then
    kill "$watcher" 2>/dev/null || true
    wait "$watcher" 2>/dev/null || true
    return 0
  fi

  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true
  echo "Auto-attach log:"
  cat /tmp/vector-autoattach.log 2>/dev/null || true
  return 1
}

main() {
  if have_serial; then
    echo "USB serial already present: $(serial_ports)"
    return 0
  fi

  local usbipd
  if ! usbipd="$(find_usbipd)"; then
    echo "usbipd.exe not found. Install usbipd-win, or attach the device manually."
    echo "  https://github.com/dorssel/usbipd-win"
    return 1
  fi

  echo "No serial device yet. Looking for the flight controller via usbipd..."
  local row bus kind stuck=""
  row="$(find_fc "$usbipd" || true)"
  kind="${row#*$'\t'}"

  if [[ -n "$row" && "$kind" == "failed" ]]; then
    stuck="${row%%$'\t'*}"
  fi

  # Plugged in but never enumerated: nothing to attach, so reset first.
  if [[ -z "$row" ]] && persisted_fc "$usbipd"; then
    echo "The board is remembered but not live; the USB port needs a reset."
    stuck="${stuck:-$FALLBACK_BUSID}"
    run_reset_elevated || true
    sleep 2
    row="$(find_fc "$usbipd" || true)"
    kind="${row#*$'\t'}"
  fi

  if [[ -n "$row" && "$kind" == "failed" ]]; then
    bus="${row%%$'\t'*}"
    stuck="$bus"
    echo "Stuck enumeration on busid=$bus (descriptor request failed)."
    run_reset_elevated || true
    sleep 2
    if have_serial; then
      echo "USB ready: $(serial_ports)"
      return 0
    fi
    row="$(find_fc "$usbipd" || true)"
    kind="${row#*$'\t'}"
  fi

  if [[ -n "$row" && "$kind" == "healthy" ]]; then
    bus="${row%%$'\t'*}"
    attach_bus "$usbipd" "$bus" || return 1
    wait_for_serial && return 0
  fi

  if persisted_fc "$usbipd" || [[ -n "$stuck" ]]; then
    await_replug "$usbipd" "${stuck:-$FALLBACK_BUSID}" && return 0
  fi

  echo "Could not recover the USB link."
  echo
  "$usbipd" list || true
  cat <<'EOF'

Manual recovery:
  1. Unplug and replug the flight controller's USB cable
  2. In an elevated PowerShell:  usbipd bind --busid <BUSID>
  3.                             usbipd attach --wsl --busid <BUSID>
  4. Re-run this script
EOF
  return 1
}

main "$@"
