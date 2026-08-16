#!/usr/bin/env bash
# Build ArduCopter for MatekH743 and flash it.
#
# WSL2 note: ./waf --upload uses Windows python.exe + COM ports (not usbipd).
# Install once on Windows:
#   python.exe -m pip install empy==3.3.4 pyserial
#
# Keep Mission Planner closed while flashing. Leave the board on Windows USB
# (do NOT usbipd-attach it for this script).
#
# Usage:
#   ./scripts/build_and_flash.sh              # configure, build, flash
#   ./scripts/build_and_flash.sh --build-only
#   ./scripts/build_and_flash.sh --force       # pass --upload-force
#   ./scripts/build_and_flash.sh --board MatekH743 --vehicle copter
#   ./scripts/build_and_flash.sh --port COM5   # Windows COM port for upload

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Prefer ArduPilot ARM GCC if installed under ~/opt (no sudo install path)
ARM_BIN="${HOME}/opt/gcc-arm-none-eabi-10-2020-q4-major/bin"
if [[ -x "${ARM_BIN}/arm-none-eabi-gcc" ]]; then
    export PATH="${ARM_BIN}:${PATH}"
fi

BOARD="MatekH743"
VEHICLE="copter"
BUILD_ONLY=0
FORCE=0
PORT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-only) BUILD_ONLY=1; shift ;;
        --force) FORCE=1; shift ;;
        --board) BOARD="$2"; shift 2 ;;
        --vehicle) VEHICLE="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        -h|--help)
            sed -n '2,18p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown arg: $1" >&2
            exit 1
            ;;
    esac
done

case "$VEHICLE" in
    copter|plane|rover|sub|heli|antennatracker|blimp) ;;
    *)
        echo "Unsupported vehicle: $VEHICLE" >&2
        exit 1
        ;;
esac

if [[ ! -x ./waf ]]; then
    echo "Not an ArduPilot tree (./waf missing): $ROOT" >&2
    exit 1
fi

echo "==> ./waf configure --board ${BOARD}"
./waf configure --board "$BOARD"

echo "==> ./waf ${VEHICLE}"
./waf "$VEHICLE"

case "$VEHICLE" in
    copter) APJ="build/${BOARD}/bin/arducopter.apj" ;;
    plane) APJ="build/${BOARD}/bin/arduplane.apj" ;;
    rover) APJ="build/${BOARD}/bin/ardurover.apj" ;;
    sub) APJ="build/${BOARD}/bin/ardusub.apj" ;;
    heli) APJ="build/${BOARD}/bin/arducopter-heli.apj" ;;
    antennatracker) APJ="build/${BOARD}/bin/antennatracker.apj" ;;
    blimp) APJ="build/${BOARD}/bin/blimp.apj" ;;
esac

if [[ ! -f "$APJ" ]]; then
    APJ="$(ls -1 "build/${BOARD}/bin/"*.apj 2>/dev/null | head -1 || true)"
fi

if [[ -z "${APJ:-}" || ! -f "$APJ" ]]; then
    echo "Build finished but no .apj found under build/${BOARD}/bin/" >&2
    exit 1
fi

echo "==> built: $APJ"

if [[ "$BUILD_ONLY" -eq 1 ]]; then
    echo "Build-only mode; not flashing."
    exit 0
fi

UPLOAD_ARGS=(--upload)
if [[ "$FORCE" -eq 1 ]]; then
    UPLOAD_ARGS+=(--upload-force)
fi
if [[ -n "$PORT" ]]; then
    UPLOAD_ARGS+=(--upload-port "$PORT")
fi

echo "==> ./waf ${VEHICLE} ${UPLOAD_ARGS[*]}"
echo "    (WSL2: uses Windows python.exe / COM ports — leave board on Windows USB, not usbipd-attached)"
./waf "$VEHICLE" "${UPLOAD_ARGS[@]}"

echo "Done."
