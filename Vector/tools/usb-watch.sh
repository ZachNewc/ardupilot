#!/usr/bin/env bash
# Watch the Windows USB tree for any change while you power-cycle the flight controller.
#
# Prints a line only when the device set changes, so you can run the BOOT/RST sequence
# with both hands and read the result afterwards. Ctrl-C to stop.
#
# Recovery identities worth waiting for:
#   1209:5741  ArduPilot bootloader  -> shows up under Ports (COM), name "ArduPilot"
#   0483:df11  STM32 system DFU      -> shows up under USB devices, "STM32 BOOTLOADER"
#   1209:5740  ArduPilot firmware    -> MAVLink + SLCAN, the healthy state
set -u

USBIPD='/mnt/c/Program Files/usbipd-win/usbipd.exe'
[ -x "$USBIPD" ] || USBIPD='/mnt/c/Program Files (x86)/usbipd-win/usbipd.exe'

snapshot() {
    "$USBIPD" list 2>/dev/null | tr -d '\r' | sed -n '/^Connected:/,/^$/p' | tail -n +3 | grep -v '^$' 
}

echo "=== watching Windows USB (Ctrl-C to stop) ==="
echo "waiting for a change; run your BOOT/RST sequence now"
prev="$(snapshot)"
echo "$prev" | sed 's/^/  baseline  /'

while true; do
    sleep 1
    cur="$(snapshot)"
    if [ "$cur" != "$prev" ]; then
        ts="$(date +%H:%M:%S)"
        diff <(printf '%s\n' "$prev") <(printf '%s\n' "$cur") \
            | grep -E '^[<>]' \
            | sed "s/^< /  $ts  GONE     /; s/^> /  $ts  APPEARED /"
        printf '%s\n' "$cur" | grep -iE '1209|0483|ardupilot|stm32|bootloader|dfu' \
            | grep -vi 'camera' \
            | sed "s/^/  $ts  >>> FC:  /"
        prev="$cur"
    fi
done
