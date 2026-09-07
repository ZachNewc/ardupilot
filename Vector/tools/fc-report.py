#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Read-only report of what the flight controller actually believes.

Writes nothing. No parameter is set, no output is driven, no motor is spun. Run it
when the dashboard commands appear to do nothing, because the usual cause is that the
board disagrees with the config about which pin does what -- or that outputs are muted
entirely and every command is being accepted and discarded.

    Vector/tools/fc-report.py                     # /dev/ttyACM0
    Vector/tools/fc-report.py --device /dev/ttyACM1

ArduPilot usually presents two USB endpoints. If the dashboard is holding ACM0, point
this at ACM1 and both can run at once.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboard"))

# Parameters that decide whether an output does anything at all.
FRAME = ["FRAME_CLASS", "FRAME_TYPE", "MOT_PWM_TYPE", "MOT_SAFE_DISARM"]
SAFETY = ["BRD_SAFETY_DEFLT", "BRD_SAFETYOPTION", "BRD_SAFETY_MASK"]
DSHOT = ["SERVO_DSHOT_ESC", "SERVO_DSHOT_RATE", "SERVO_BLH_MASK", "SERVO_BLH_RVMASK",
         "SERVO_BLH_AUTO", "SERVO_BLH_TRATE"]
CAN = ["CAN_P1_DRIVER", "CAN_D1_PROTOCOL", "CAN_D1_UC_SRV_BM", "CAN_D1_UC_ESC_BM",
       "CAN_D1_UC_ESC_OF", "CAN_D1_UC_OPTION", "CAN_D1_UC_SRV_RT", "SERVO_32_ENABLE"]
# Through the node's fourth output. S17 only exists once SERVO_32_ENABLE is set, so an
# unreadable S17 with that at 0 is expected rather than a fault.
CHANNELS = 17

# SERVOn_FUNCTION values worth naming. Motor1..Motor8 are 33..40.
FUNCTIONS = {0: "Disabled", 1: "RCPassThru", 51: "RCIN1", 52: "RCIN2"}
FUNCTIONS.update({33 + i: f"Motor{i + 1}" for i in range(8)})
FUNCTIONS.update({94 + i: f"Scripting{i + 1}" for i in range(8)})

# Timer groups on the MatekH743. Every output in a group shares one output mode, so a
# group carrying both a servo and a DShot motor cannot satisfy both. Imported rather
# than restated: a second copy of this table is how a servo ends up on a pin that
# physically cannot pulse it.
from server import board                                              # noqa: E402

TIMER_GROUPS = {group.name: list(group.channels) for group in board.TIMER_GROUPS}


def fetch_params(conn, names, rounds=6, batch=12) -> Dict[str, Optional[float]]:
    """
    Read parameters by name, in small batches with retries.

    Asking for everything in one burst loses replies: the responses arrive faster than
    the link drains and the overflow is silent, which reads identically to a parameter
    that does not exist. Batching and retrying keeps "absent" meaningful.
    """
    wanted = list(dict.fromkeys(names))
    found: Dict[str, Optional[float]] = {}

    for _ in range(rounds):
        if not wanted:
            break
        still_missing = []
        for start in range(0, len(wanted), batch):
            chunk = set(wanted[start:start + batch])
            for name in chunk:
                conn.mav.param_request_read_send(
                    conn.target_system, conn.target_component, name.encode("ascii"), -1
                )
            deadline = time.time() + 2.5
            while chunk and time.time() < deadline:
                msg = conn.recv_match(type="PARAM_VALUE", blocking=True, timeout=0.5)
                if msg is None:
                    continue
                got = msg.param_id
                if not isinstance(got, str):
                    got = got.decode("ascii", "ignore")
                got = got.rstrip("\x00")
                if got in chunk:
                    found[got] = msg.param_value
                    chunk.discard(got)
            still_missing.extend(sorted(chunk))
        wanted = still_missing

    for name in wanted:
        found[name] = None
    return found


def report_config_diff(values: Dict[str, Optional[float]]) -> None:
    """
    Diff the board against vector.json for the live arms.

    This is the check that matters. Every failure so far has been the board quietly
    disagreeing with the config -- an output left Disabled, or a pulse window still at
    the 1000-2000 default clamping a 500-2500 gimbal -- and neither reports an error,
    so the only symptom is hardware that does not move.
    """
    try:
        from server import config as vconfig
        cfg = vconfig.load()
    except Exception as exc:                                     # pragma: no cover
        print(f"\nCould not load vector.json for comparison: {exc}")
        return

    def expect(name: str, want: float, why: str) -> Optional[str]:
        got = values.get(name)
        if got is None:
            return f"  {name:22} unreadable, expected {int(want)} ({why})"
        if int(got) != int(want):
            return f"  {name:22} board {int(got):<6} config wants {int(want):<6} ({why})"
        return None

    print("\nBoard vs vector.json (live arms)")
    problems = []

    # The motor function numbers are only meaningful for the frame they came from, so a
    # frame mismatch invalidates every one of them at once.
    frame = (cfg.raw.get("vehicle") or {}).get("frame") or {}
    frame_problems = []
    for key, param in (("frame_class", "FRAME_CLASS"), ("frame_type", "FRAME_TYPE")):
        want = frame.get(key)
        if want is None:
            continue
        issue = expect(param, float(want), "motor numbers are frame-specific")
        if issue:
            frame_problems.append(issue)
    problems.extend(frame_problems)
    for arm in cfg.live_arms():
        for name in ("outer", "inner"):
            axis = arm.gimbal.axis(name)
            ch = axis.channel
            if ch < 1:
                continue
            for suffix, want, why in (
                ("FUNCTION", 0, "gimbals need Disabled for DO_SET_SERVO"),
                ("MIN", axis.min_us, "clamps the gimbal window"),
                ("MAX", axis.max_us, "clamps the gimbal window"),
                ("TRIM", axis.center_us, "rest position"),
                ("REVERSED", 0, "axis sign belongs in vector.json"),
            ):
                issue = expect(f"SERVO{ch}_{suffix}", float(want), f"{arm.id} {name}, {why}")
                if issue:
                    problems.append(issue)
        for name, motor in arm.motors.items():
            if motor.channel < 1:
                continue
            if motor.function is None:
                problems.append(
                    f"  {arm.id}.{name:6} (S{motor.channel}) has no function in vector.json"
                )
                continue
            issue = expect(
                f"SERVO{motor.channel}_FUNCTION", float(motor.function),
                f"{arm.id} {name} is Motor{motor.function - 32}",
            )
            if issue:
                problems.append(issue)

    if not problems:
        print("  Board agrees with the config.")
        return

    print("\n".join(problems))
    outputs = len(problems) - len(frame_problems)
    print(f"\n  {len(problems)} disagreement(s).")
    if outputs:
        print(f"  {outputs} output setting(s): connecting the dashboard asserts these.")
    if frame_problems:
        print("  FRAME_CLASS/FRAME_TYPE: set by hand and reboot. The dashboard will not")
        print("  write these, because changing the frame silently would rearrange which")
        print("  physical motor answers to which mixer slot.")


def report_output_map() -> None:
    """
    Check vector.json against what the hardware can physically do.

    Distinct from the board diff above, which compares parameters. This finds maps that
    cannot work no matter what the parameters say: a servo sharing a timer with an ESC,
    or a mixer slot the config leaves for the firmware to place, which lands it on a low
    channel at boot and takes that whole timer group into DShot.
    """
    try:
        from server import config as vconfig
        cfg = vconfig.load()
    except Exception as exc:                                         # pragma: no cover
        print(f"\nCould not check the output map: {exc}")
        return

    print("\nOutput map (vector.json against the board's timer groups)")
    print(board.summary(cfg))

    problems = board.check_output_map(cfg)
    if not problems:
        print("\n  Every group is single-mode and every mixer slot is placed.")
        return
    print()
    for problem in problems:
        print(f"  [{problem.severity.upper()}] S{problem.channel}: {problem.text}")


def report_directions() -> None:
    """
    Check the recorded rotation directions against the frame's mixer table.

    Unlike everything else here this compares the config to the firmware, not to the
    board: the flight controller has no opinion on which way a propeller turns. It is
    reported here because this is where people look, and because a wrong direction is
    silent on the bench -- the motor spins, it just subtracts from yaw authority and its
    coaxial partner no longer cancels its torque.
    """
    try:
        from server import config as vconfig
        from server import frames
        cfg = vconfig.load()
    except Exception as exc:                                         # pragma: no cover
        print(f"\nCould not check rotation directions: {exc}")
        return

    frame = (cfg.raw.get("vehicle") or {}).get("frame") or {}
    slots = frames.table(frame.get("frame_class"), frame.get("frame_type"))
    print("\nRotation direction (vector.json against the frame's mixer table)")
    if slots is None:
        print("  Unknown frame, so the required directions cannot be established.")
        return

    wrong = []
    for arm in cfg.live_arms():
        for name, motor in arm.motors.items():
            if motor.function is None or motor.channel < 1:
                continue
            slot = slots.get(motor.function - 32)
            if slot is not None and slot.spin != motor.spin:
                wrong.append(
                    f"  {arm.id}.{name:7} S{motor.channel:<3} Motor{slot.number}  "
                    f"recorded {motor.spin:4} frame wants {slot.spin}"
                )
    if not wrong:
        print("  Every live motor is recorded turning the way the frame wants.")
        return
    print("\n".join(wrong))
    print("  Flip `reversed` on those motors and reboot. If the recorded direction is")
    print("  stale, establish it with Vector/tools/motor-direction.py.")


def show(title: str, values: Dict[str, Optional[float]], names) -> None:
    print(f"\n{title}")
    for name in names:
        value = values.get(name)
        if value is None:
            print(f"  {name:20} not present on this firmware")
        elif value == int(value):
            print(f"  {name:20} {int(value)}")
        else:
            print(f"  {name:20} {value}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    from pymavlink import mavutil

    print(f"Connecting to {args.device}...")
    conn = mavutil.mavlink_connection(args.device, baud=args.baud, source_system=254)
    if conn.wait_heartbeat(timeout=10) is None:
        print("No heartbeat. Nothing else here will work; check the cable and the port.")
        return 1
    print(f"Heartbeat from system {conn.target_system} component {conn.target_component}")

    per_channel = [
        f"SERVO{n}_{suffix}"
        for n in range(1, CHANNELS + 1)
        for suffix in ("FUNCTION", "MIN", "MAX", "TRIM", "REVERSED")
    ]
    values = fetch_params(conn, FRAME + SAFETY + DSHOT + CAN + per_channel)

    show("Frame", values, FRAME)
    show("Safety", values, SAFETY)
    show("DShot", values, DSHOT)
    show("CAN (DroneCAN PWM expander)", values, CAN)
    esc_bm = values.get("CAN_D1_UC_ESC_BM")
    if esc_bm is not None and int(esc_bm) == 0:
        print("  CAN_D1_UC_ESC_BM is 0 -- the CAN-to-PWM node is never sent an ESC")
        print("  command, so South and West motors cannot turn. Re-apply output mapping.")
    esc_of = values.get("CAN_D1_UC_ESC_OF")
    if esc_of is not None and int(esc_of) != board.CAN_ESC_OFFSET:
        print(f"  CAN_D1_UC_ESC_OF is {int(esc_of)}, expected {board.CAN_ESC_OFFSET}: the")
        print("  node's outputs answer the wrong RawCommand slots. Re-apply output mapping.")
    print("  Note: a CAN ESC receives zero unless the vehicle is soft-armed. On the bench")
    print("  only the motor test soft-arms, so the dashboard spins those one at a time.")
    protocol = values.get("CAN_D1_PROTOCOL")
    if protocol is not None and int(protocol) != 1:
        print("  CAN_D1_PROTOCOL is not DroneCAN (1). The expander needs that, then a reboot.")

    # The gimbal needs the full 500-2500 window. SERVOn_MIN/MAX clamp DO_SET_SERVO, so
    # a default 1000-2000 output silently throws away the outer half of the travel.
    print("\nOutput functions and pulse limits")
    print(f"  {'ch':4} {'func':>5}  {'name':12} {'min':>5} {'max':>5} {'trim':>5} {'rev':>4}")
    functions: Dict[int, Optional[float]] = {}
    for n in range(1, CHANNELS + 1):
        raw = values.get(f"SERVO{n}_FUNCTION")
        functions[n] = raw
        if raw is None:
            print(f"  S{n:<3} SERVO{n}_FUNCTION not readable")
            continue

        def field(suffix: str) -> str:
            got = values.get(f"SERVO{n}_{suffix}")
            return "-" if got is None else str(int(got))

        code = int(raw)
        name = FUNCTIONS.get(code, "other")
        window = ""
        lo, hi = values.get(f"SERVO{n}_MIN"), values.get(f"SERVO{n}_MAX")
        if lo is not None and hi is not None and (lo > 500 or hi < 2500):
            window = "  <-- clamps the 500-2500 gimbal window"
        print(
            f"  S{n:<3} {code:>5}  {name:12} "
            f"{field('MIN'):>5} {field('MAX'):>5} {field('TRIM'):>5} {field('REVERSED'):>4}{window}"
        )

    # A group mixing a motor with a non-motor cannot work, whatever the params say.
    print("\nTimer groups")
    for group, channels in TIMER_GROUPS.items():
        motors, others = [], []
        for n in channels:
            raw = functions.get(n)
            if raw is None:
                continue
            code = int(raw)
            if 33 <= code <= 40:
                motors.append(f"S{n}")
            elif code != 0:
                others.append(f"S{n}")
        note = ""
        if motors and others:
            note = f"  <-- CONFLICT: motors {','.join(motors)} share this group with {','.join(others)}"
        listing = ", ".join(f"S{n}" for n in channels)
        print(f"  {group:6} {listing:22}{note}")

    print("\nNote: a Disabled output is what the dashboard needs for DO_SET_SERVO, so")
    print("Disabled on a gimbal channel is correct. Disabled on a motor channel means")
    print("that motor has no output at all.")

    report_config_diff(values)
    report_output_map()
    report_directions()

    # Live output values prove whether anything is actually reaching the pins.
    print("\nWaiting for SERVO_OUTPUT_RAW...")
    conn.mav.request_data_stream_send(
        conn.target_system, conn.target_component,
        mavutil.mavlink.MAV_DATA_STREAM_ALL, 4, 1,
    )
    raw = conn.recv_match(type="SERVO_OUTPUT_RAW", blocking=True, timeout=5.0)
    if raw is None:
        print("  none received - the board is not reporting its outputs")
    else:
        pwm = [getattr(raw, f"servo{n}_raw", 0) for n in range(1, CHANNELS + 1)]
        print("  " + "  ".join(f"S{n}={v}" for n, v in enumerate(pwm, start=1)))
        if all(v == 0 for v in pwm):
            print("  Every output reads 0. Outputs are muted -- safety engaged is the")
            print("  usual cause, and every command will be accepted and discarded.")

    heartbeat = conn.recv_match(type="HEARTBEAT", blocking=True, timeout=3.0)
    if heartbeat is not None:
        armed = bool(heartbeat.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
        print(f"\nArmed: {armed}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
