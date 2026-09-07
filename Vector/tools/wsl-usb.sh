#!/usr/bin/env bash
# Attach the flight controller's USB serial port to WSL.
#
# WSL does not see USB devices until usbipd-win shares and attaches them, and the
# H743's CDC interface sometimes enumerates as "Device Descriptor Request Failed",
# which needs a ghost-node removal and a physical replug to clear. A leftover
# /dev/ttyACM* node is not enough: after a reboot the board is often Shared on
# Windows but not Attached here, and a lone ACM1 is the dead SLCAN half of that.
# This script walks that recovery so the dashboard can just be started.
#
#   Vector/tools/wsl-usb.sh
#   USB_MATCH='VID:PID' Vector/tools/wsl-usb.sh
#
# On a native Linux host this is unnecessary: plug the board in and the port appears.
set -euo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VECTOR="$(cd "$TOOLS/.." && pwd)"
ATTACH_TIMEOUT_S="${ATTACH_TIMEOUT_S:-30}"
BUSID_CACHE="${BUSID_CACHE:-/tmp/vector-usb-busid}"

# ArduPilot's CDC VID:PID, plus Matek's vendor ID and the description strings.
USB_MATCH="${USB_MATCH:-ArduPilot|Matek|1209:5740|2DAE:}"

# Last-known busid only. Never guess a port number; this board moves (2-5, 2-6, ...).
FALLBACK_BUSID="${FALLBACK_BUSID:-}"

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

configured_serial() {
  python3 -c '
import json, sys
path = sys.argv[1]
try:
    print(json.load(open(path, encoding="utf-8"))["link"]["device"])
except Exception:
    print("/dev/ttyACM0")
' "$VECTOR/config/vector.json"
}

serial_ports() {
  local path found=()
  for path in /dev/ttyACM* /dev/ttyUSB*; do
    [[ -e "$path" ]] && found+=("$path")
  done
  ((${#found[@]})) && printf '%s\n' "${found[*]}"
}

# True when the MAVLink CDC can be opened. A lone leftover ACM1 (SLCAN) is not enough.
serial_usable() {
  local want
  want="$(configured_serial)"
  python3 -c '
import glob, sys

want = sys.argv[1]
try:
    import serial
except ImportError:
    sys.exit(1)

ports = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
ordered = ([want] if want in ports else []) + [p for p in ports if p != want]
# After a dead attach the SLCAN half (ACM1) often remains. That is not the link.
if want not in ports and ordered == ["/dev/ttyACM1"]:
    sys.exit(1)

for path in ordered:
    if path == "/dev/ttyACM1" and want != path:
        continue
    try:
        handle = serial.Serial(path, 115200, timeout=0.2)
        handle.close()
        print(path)
        sys.exit(0)
    except Exception:
        continue
sys.exit(1)
' "$want"
}

usbipd_state() {
  "$1" state 2>/dev/null || echo '{"Devices":[]}'
}

# Print "BUSID<TAB>healthy|failed<TAB>attached|shared|notshared" or nothing.
find_fc() {
  usbipd_state "$1" | python3 -c '
import json, re, sys

data = json.load(sys.stdin)
pattern = re.compile(sys.argv[1], re.I)
healthy = failed = None

def attach_state(device):
    if device.get("ClientIPAddress"):
        return "attached"
    if device.get("PersistedGuid"):
        return "shared"
    return "notshared"

for device in data.get("Devices", []):
    bus = device.get("BusId")
    if not bus:
        continue
    description = device.get("Description") or ""
    instance = (device.get("InstanceId") or "").upper()
    match = re.search(r"VID_([0-9A-F]+)&PID_([0-9A-F]+)", instance)
    vidpid = f"{match.group(1)}:{match.group(2)}" if match else ""
    row = (bus, attach_state(device))

    # A stuck enumeration reports a failed descriptor and a null VID:PID.
    if "Descriptor Request Failed" in description or vidpid in ("0000:0002", "0000:0000"):
        failed = failed or row
        continue
    if pattern.search(f"{description} {vidpid} {instance}"):
        healthy = row
        break

pick, kind = (healthy, "healthy") if healthy else (failed, "failed")
if pick:
    print(f"{pick[0]}\t{kind}\t{pick[1]}")
' "$USB_MATCH"
}

# True when usbipd remembers a board that is not currently enumerated as ArduPilot.
persisted_ghost() {
  usbipd_state "$1" | python3 -c '
import json, sys

data = json.load(sys.stdin)
for device in data.get("Devices", []):
    description = device.get("Description") or ""
    instance = (device.get("InstanceId") or "").upper()
    if not device.get("PersistedGuid"):
        continue
    if device.get("BusId"):
        continue
    if "Descriptor Request Failed" in description or "VID_0000&PID_0002" in instance:
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

remember_bus() {
  printf '%s\n' "$1" > "$BUSID_CACHE"
}

cached_bus() {
  if [[ -n "$FALLBACK_BUSID" ]]; then
    printf '%s\n' "$FALLBACK_BUSID"
    return
  fi
  [[ -r "$BUSID_CACHE" ]] && cat "$BUSID_CACHE"
}

wait_for_serial() {
  local timeout_s="${1:-$ATTACH_TIMEOUT_S}" i port
  printf 'Waiting for serial device'
  for ((i = 0; i < timeout_s; i++)); do
    if port="$(serial_usable)"; then
      printf '\nUSB ready: %s\n' "$port"
      return 0
    fi
    printf '.'
    sleep 1
  done
  printf '\n'
  local leftover
  leftover="$(serial_ports || true)"
  if [[ -n "$leftover" ]]; then
    echo "Nodes present but not usable as MAVLink: $leftover"
  fi
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
  remember_bus "$bus"

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

cycle_attach() {
  local usbipd="$1" bus="$2"
  echo "Cycling usbipd attach on busid=$bus (Attached in Windows, no working serial here)..."
  "$usbipd" detach --busid "$bus" 2>/dev/null || true
  sleep 1
  attach_bus "$usbipd" "$bus"
}

# Watch for a healthy board on any busid. This H743 moves between 2-5 and 2-6;
# pinning auto-attach to the stuck busid is how a replug was missed.
watch_and_attach() {
  local usbipd="$1" timeout_s="${2:-$ATTACH_TIMEOUT_S}" i row port
  printf 'Watching for the flight controller'
  for ((i = 0; i < timeout_s; i++)); do
    if port="$(serial_usable)"; then
      printf '\nUSB ready: %s\n' "$port"
      return 0
    fi
    row="$(find_fc "$usbipd" || true)"
    parse_row "$row" || true
    if [[ "$kind" == "healthy" && -n "$bus" ]]; then
      printf '\n'
      echo "Board came up on busid=$bus. Attaching..."
      remember_bus "$bus"
      attach_bus "$usbipd" "$bus" || true
      wait_for_serial 8 && return 0
    fi
    printf '.'
  done
  printf '\n'
  return 1
}

# Arm auto-attach on the last known bus, and also poll every busid — the board
# often reappears on a neighbour port after a descriptor failure.
await_replug() {
  local usbipd="$1" bus="$2" watcher
  cat <<EOF

USB needs a physical replug if the hub cycle did not clear it.
  1. Unplug the flight controller's USB cable
  2. Plug it back in (same port is fine; 2-5 and 2-6 are both handled)
Watching for ${ATTACH_TIMEOUT_S}s...
EOF

  if [[ -n "$bus" ]]; then
    "$usbipd" bind --busid "$bus" 2>/dev/null || true
    "$usbipd" attach --wsl --busid "$bus" --auto-attach --unplugged \
      >/tmp/vector-autoattach.log 2>&1 &
    watcher=$!
  fi

  if watch_and_attach "$usbipd" "$ATTACH_TIMEOUT_S"; then
    [[ -n "${watcher:-}" ]] && kill "$watcher" 2>/dev/null || true
    [[ -n "${watcher:-}" ]] && wait "$watcher" 2>/dev/null || true
    return 0
  fi

  [[ -n "${watcher:-}" ]] && kill "$watcher" 2>/dev/null || true
  [[ -n "${watcher:-}" ]] && wait "$watcher" 2>/dev/null || true
  echo "Auto-attach log:"
  cat /tmp/vector-autoattach.log 2>/dev/null || true
  return 1
}

parse_row() {
  # Sets bus, kind, attach from a find_fc line.
  local row="$1"
  bus=""
  kind=""
  attach=""
  [[ -z "$row" ]] && return 1
  bus="${row%%$'\t'*}"
  local rest="${row#*$'\t'}"
  kind="${rest%%$'\t'*}"
  attach="${rest#*$'\t'}"
}

main() {
  local usbipd
  if ! usbipd="$(find_usbipd)"; then
    if serial_usable >/dev/null; then
      echo "USB serial already present: $(serial_usable)"
      return 0
    fi
    echo "usbipd.exe not found. Install usbipd-win, or attach the device manually."
    echo "  https://github.com/dorssel/usbipd-win"
    return 1
  fi

  local row bus kind attach stuck=""
  row="$(find_fc "$usbipd" || true)"
  parse_row "$row" || true

  if [[ "$kind" == "healthy" && -n "$bus" ]]; then
    remember_bus "$bus"
    if [[ "$attach" == "attached" ]] && serial_usable >/dev/null; then
      echo "USB serial already present: $(serial_usable)"
      return 0
    fi
    echo "Flight controller on busid=$bus ($attach). Bringing it into WSL..."
    if [[ "$attach" == "attached" ]]; then
      cycle_attach "$usbipd" "$bus" || true
    else
      attach_bus "$usbipd" "$bus" || return 1
    fi
    wait_for_serial && return 0
    echo "Attach reported success but $(configured_serial) is not usable. Retrying..."
    cycle_attach "$usbipd" "$bus" || true
    wait_for_serial && return 0
  fi

  if [[ "$kind" == "failed" && -n "$bus" ]]; then
    stuck="$bus"
    echo "Stuck enumeration on busid=$bus (descriptor request failed)."
    run_reset_elevated || true
    sleep 2
    if wait_for_serial 5; then
      return 0
    fi
    row="$(find_fc "$usbipd" || true)"
    parse_row "$row" || true
    if [[ "$kind" == "healthy" && -n "$bus" ]]; then
      remember_bus "$bus"
      attach_bus "$usbipd" "$bus" || true
      wait_for_serial && return 0
    fi
  fi

  if persisted_ghost "$usbipd"; then
    echo "A failed USB descriptor is still persisted; the port needs a reset."
    stuck="${stuck:-$(cached_bus)}"
    run_reset_elevated || true
    sleep 2
    row="$(find_fc "$usbipd" || true)"
    parse_row "$row" || true
    if [[ "$kind" == "healthy" && -n "$bus" ]]; then
      remember_bus "$bus"
      attach_bus "$usbipd" "$bus" || true
      wait_for_serial && return 0
    fi
  fi

  if [[ -n "$stuck" || -n "$(cached_bus)" ]]; then
    await_replug "$usbipd" "${stuck:-$(cached_bus)}" && return 0
  fi

  echo "Could not recover the USB link."
  echo
  "$usbipd" list || true
  cat <<'EOF'

Manual recovery:
  1. Unplug and replug the flight controller's USB cable
  2. In an elevated PowerShell:  usbipd bind --busid <BUSID>
  3.                             usbipd attach --wsl --busid <BUSID>
  4. Re-run Vector/start.sh
EOF
  return 1
}

main "$@"
