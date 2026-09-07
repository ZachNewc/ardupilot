#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Force timer groups off leftover DShot, then put DShot only on TIM5.

Mission Planner setting MOT_PWM_TYPE to 0 often does not survive a USB unplug:
0 is the firmware default, and a hard power cut can happen before the save
finishes. This script writes OneShot (1) first so the value must be stored,
reboots with a MAVLink reboot (which flushes the save), then writes DShot600
(6) and reboots again.

Close Mission Planner first. It holds the Windows COM port.

    Vector/tools/pwm-mode-reset.sh
    python3 Vector/tools/pwm-mode-reset.py --device /dev/ttyACM0
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from typing import Dict, List, Optional, Tuple

TOOLS = os.path.dirname(os.path.abspath(__file__))
VECTOR = os.path.dirname(TOOLS)
CONFIG_PATH = os.path.join(VECTOR, "config", "vector.json")

# 1 = OneShot: analog pulses, not the firmware default, so it is actually saved.
PWM_CLEAR = 1.0
PWM_DSHOT = 6.0
SAVE_WAIT_S = 8.0
REBOOT_WAIT_S = 18.0
HEARTBEAT_S = 20.0

MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN = 246
MAV_CMD_DO_SEND_BANNER = 42428


def load_map() -> Tuple[Dict[int, float], str]:
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        doc = json.load(handle)
    functions: Dict[int, float] = {}
    for arm in doc.get("arms") or []:
        for axis in ("inner", "outer"):
            channel = int((arm.get(axis) or {}).get("channel") or 0)
            if channel > 0:
                functions[channel] = 0.0
        for motor in (arm.get("motors") or {}).values():
            channel = int(motor.get("channel") or 0)
            function = motor.get("function")
            if channel > 0 and function is not None:
                functions[channel] = float(function)
    device = str((doc.get("link") or {}).get("device") or "/dev/ttyACM0")
    return functions, device


def attach_usb() -> None:
    script = os.path.join(TOOLS, "wsl-usb.sh")
    if not os.path.isfile(script):
        return
    print("Attaching USB...")
    subprocess.run(["bash", script], check=False)


def connect(device: str, baud: int):
    from pymavlink import mavutil

    print(f"Connecting {device}...")
    try:
        conn = mavutil.mavlink_connection(device, baud=baud, source_system=254)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot open {device}: {exc}. Close Mission Planner completely "
            "(Disconnect, then quit the app) and run this again."
        ) from exc
    if conn.wait_heartbeat(timeout=HEARTBEAT_S) is None:
        raise RuntimeError(f"No heartbeat on {device}. Close Mission Planner and retry.")
    print(f"Heartbeat system {conn.target_system} component {conn.target_component}")
    return conn


def drain(conn) -> None:
    while conn.recv_match(blocking=False) is not None:
        pass


def get_param(conn, name: str, timeout: float = 4.0) -> Optional[float]:
    drain(conn)
    conn.mav.param_request_read_send(
        conn.target_system, conn.target_component, name.encode("ascii"), -1
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.4)
        if msg is None:
            continue
        got = msg.param_id
        if not isinstance(got, str):
            got = bytes(got).decode("ascii", errors="ignore")
        got = got.split("\x00", 1)[0]
        if got == name:
            return float(msg.param_value)
    return None


def set_param(conn, name: str, value: float) -> float:
    from pymavlink import mavutil

    conn.mav.param_set_send(
        conn.target_system,
        conn.target_component,
        name.encode("ascii"),
        float(value),
        mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
    )
    got = get_param(conn, name)
    if got is None:
        raise RuntimeError(f"{name} write was not acknowledged")
    print(f"  {name} = {int(got) if got == int(got) else got}")
    return got


def banner(conn) -> str:
    conn.mav.command_long_send(
        conn.target_system, conn.target_component,
        MAV_CMD_DO_SEND_BANNER, 0, 0, 0, 0, 0, 0, 0, 0,
    )
    deadline = time.time() + 8.0
    while time.time() < deadline:
        msg = conn.recv_match(type="STATUSTEXT", blocking=True, timeout=0.4)
        if msg is None:
            continue
        text = msg.text
        if not isinstance(text, str):
            text = bytes(text).decode("ascii", errors="ignore")
        if "RCOut" in text:
            print(f"  {text.strip()}")
            return text
    print("  (no RCOut banner)")
    return ""


def apply_functions(conn, functions: Dict[int, float]) -> None:
    print("Writing output functions from vector.json...")
    for channel in sorted(functions):
        set_param(conn, f"SERVO{channel}_FUNCTION", functions[channel])


def reboot_and_reconnect(conn, device: str, baud: int, expect: float, use_usb: bool):
    print(f"Waiting {SAVE_WAIT_S:.0f}s for the save to finish...")
    time.sleep(SAVE_WAIT_S)
    print("Sending MAVLink reboot...")
    conn.mav.command_long_send(
        conn.target_system, conn.target_component,
        MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN, 0, 1, 0, 0, 0, 0, 0, 0,
    )
    try:
        conn.close()
    except Exception:
        pass
    print(f"Waiting {REBOOT_WAIT_S:.0f}s for the board to come back...")
    time.sleep(REBOOT_WAIT_S)
    if use_usb:
        attach_usb()
    conn = connect(device, baud)
    got = get_param(conn, "MOT_PWM_TYPE")
    if got is None or abs(got - expect) > 0.1:
        raise RuntimeError(
            f"After reboot MOT_PWM_TYPE is {got}, expected {int(expect)}. "
            "The value still did not save."
        )
    print(f"After reboot MOT_PWM_TYPE = {int(got)}")
    return conn


def main(argv: Optional[List[str]] = None) -> int:
    functions, default_device = load_map()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=default_device)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--skip-map", action="store_true",
                        help="Do not rewrite SERVOn_FUNCTION from vector.json")
    parser.add_argument("--no-usb", action="store_true",
                        help="Do not run wsl-usb.sh (use when talking to a Windows COM port)")
    args = parser.parse_args(argv)

    if not args.no_usb:
        attach_usb()
    conn = connect(args.device, args.baud)
    print("Before:")
    current = get_param(conn, "MOT_PWM_TYPE")
    print(f"  MOT_PWM_TYPE = {current}")
    banner(conn)

    if not args.skip_map:
        apply_functions(conn, functions)

    print("Step 1: MOT_PWM_TYPE = 1 (OneShot, all analog) so leftover DShot can clear")
    set_param(conn, "MOT_PWM_TYPE", PWM_CLEAR)
    conn = reboot_and_reconnect(conn, args.device, args.baud, PWM_CLEAR, not args.no_usb)
    banner(conn)

    print("Step 2: MOT_PWM_TYPE = 6 (DShot600 on motor groups only)")
    set_param(conn, "MOT_PWM_TYPE", PWM_DSHOT)
    conn = reboot_and_reconnect(conn, args.device, args.baud, PWM_DSHOT, not args.no_usb)
    text = banner(conn)
    try:
        conn.close()
    except Exception:
        pass

    if "DS600:3-6" in text and "PWM:1-2" in text:
        print("Timer split is correct: PWM on S1-S2, DShot on S3-S6, PWM on S7+.")
        return 0
    if "RCOut" in text:
        print("Reboots finished, but the RCOut line is not the split we want.")
        print("Wanted something like: PWM:1-2 DS600:3-6 PWM:7-13")
        return 2
    print("Reboots finished. Connect the dashboard and try the servos.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
