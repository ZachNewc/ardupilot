#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Establish which way each propeller actually turns, then fix the config to match.

This is the one thing about motor direction that cannot be derived. The frame's mixer
table says which way each motor *must* turn -- every bottom motor CCW and every top motor
CW, seen from above -- but whether a given ESC produces that depends on how its three
phases happen to be wired, which is per motor and unknowable from here. So it is
observed: spin one motor, look at it, write down what happened.

    Vector/tools/motor-direction.py
    Vector/tools/motor-direction.py --device /dev/ttyACM1
    Vector/tools/motor-direction.py --arm north        # just one arm

For each live motor it spins that motor alone, asks which way it turned, and records the
answer as `spin` in vector.json. Where the observation disagrees with the frame, it flips
that motor's `reversed` flag. Nothing is written until you have seen the whole summary.

PROPELLERS MUST BE OFF. The script refuses to start until you confirm that, and every
spin is bounded by the bench limits in vector.json.

Direction is reported as seen from ABOVE the vehicle, looking down, for both motors of a
pair. The bottom motor hangs under the arm, so you are looking at the back of it -- what
matters is which way it turns about the vertical axis, not which face you can see.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "dashboard"))

CONFIG_PATH = os.path.join(HERE, "..", "config", "vector.json")


def ask(prompt: str, answers: Dict[str, str]) -> str:
    """Prompt until one of the accepted answers comes back."""
    options = "/".join(answers)
    while True:
        got = input(f"{prompt} [{options}] ").strip().lower()
        if got in answers:
            return answers[got]
        print(f"  Please answer one of: {options}")


def confirm_props_off() -> bool:
    print(__doc__.split("PROPELLERS MUST BE OFF")[0].rstrip())
    print("=" * 70)
    print("PROPELLERS MUST BE OFF. Every motor on the vehicle is about to be spun.")
    print("=" * 70)
    typed = input('Type "PROPS OFF" to continue, anything else to abort: ').strip()
    if typed != "PROPS OFF":
        print("Aborted. Nothing was spun.")
        return False
    return True


def observe(bench, arm, role, motor, slot, percent, seconds) -> Optional[str]:
    """
    Spin one motor and return the direction seen, or None if it was skipped.

    One motor at a time is the point. Spinning a pair together tells you nothing about
    which of the two is wired backwards.
    """
    label = f"{arm.id}.{role}"
    print(f"\n--- {label}  (S{motor.channel}, Motor{slot.number}, test order "
          f"{slot.test_sequence})")
    print(f"    frame wants {slot.spin.upper()} seen from above")

    while True:
        action = ask("    spin it?", {"y": "spin", "": "spin", "s": "skip", "q": "quit"})
        if action == "quit":
            return "quit"
        if action == "skip":
            return None

        try:
            bench.spin_motors([arm.id], [role], percent, seconds)
        except Exception as exc:
            print(f"    refused: {exc}")
            return None

        seen = ask("    which way did it turn, seen from above?",
                   {"cw": "cw", "ccw": "ccw", "n": "none", "r": "retry"})
        if seen == "retry":
            continue
        if seen == "none":
            print("    Did not move. That is a mapping or power problem, not a "
                  "direction one; run Vector/tools/fc-report.py.")
            return None
        mark = "ok" if seen == slot.spin else "WRONG WAY"
        print(f"    observed {seen.upper()} -- {mark}")
        return seen


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe and correct motor rotation direction.")
    parser.add_argument("--device", default="/dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--arm", action="append", dest="arms",
                        help="limit to one arm; repeatable")
    parser.add_argument("--percent", type=float, default=None,
                        help="throttle percent; defaults to the bench limit")
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args()

    from server import config as vconfig
    from server import frames
    from server.bench import Bench
    from server.link import MavlinkLink

    cfg = vconfig.load()
    frame = (cfg.raw.get("vehicle") or {}).get("frame") or {}
    slots = frames.table(frame.get("frame_class"), frame.get("frame_type"))
    if slots is None:
        print(f"No motor table for FRAME_CLASS {frame.get('frame_class')} / FRAME_TYPE "
              f"{frame.get('frame_type')}. Cannot say which direction is correct.")
        return 1

    if not confirm_props_off():
        return 1

    link = MavlinkLink()
    print(f"\n{link.connect(args.device, args.baud)}")
    bench = Bench(cfg, link=link)

    mismatch = bench.check_frame()
    if mismatch:
        print(f"\nFRAME MISMATCH: {mismatch}")
        print("Motor numbers mean something else on the board's frame. Fix that first.")
        link.disconnect()
        return 1

    percent = args.percent
    if percent is None:
        percent = cfg.bench_limits.motor_percent
    print(f"Spinning at {percent}% for {args.seconds}s, one motor at a time.\n"
          "Answers: cw, ccw, n if it did not move, r to spin again, s to skip, q to stop.")

    wanted = args.arms
    observations: List[Tuple[str, str, str, str, bool]] = []
    stopped = False
    for arm in cfg.live_arms():
        if wanted and arm.id not in wanted:
            continue
        for role, motor in arm.motors.items():
            if motor.channel < 1:
                print(f"\n--- {arm.id}.{role}: channel is 0 (disabled), skipping")
                continue
            if motor.function is None:
                print(f"\n--- {arm.id}.{role}: no function set, skipping")
                continue
            slot = slots.get(motor.function - 32)
            if slot is None:
                print(f"\n--- {arm.id}.{role}: Motor{motor.function - 32} is not on "
                      "this frame, skipping")
                continue
            seen = observe(bench, arm, role, motor, slot, percent, args.seconds)
            if seen == "quit":
                stopped = True
                break
            if seen is None:
                continue
            observations.append((arm.id, role, seen, slot.spin, motor.reversed))
        if stopped:
            break

    link.disconnect()

    if not observations:
        print("\nNothing observed, so nothing to change.")
        return 0

    # A motor turning the wrong way needs its reverse flag flipped -- whichever way it
    # currently sits. There is no absolute mapping from `reversed` to a direction,
    # because that depends on the ESC's phase order.
    print("\n" + "=" * 70)
    print(f"  {'motor':16} {'seen':5} {'wanted':6} {'reversed':>14}")
    changes: Dict[str, Dict[str, Dict[str, object]]] = {}
    for arm_id, role, seen, want, was_reversed in observations:
        now_reversed = not was_reversed if seen != want else was_reversed
        flag = f"{was_reversed} -> {now_reversed}" if now_reversed != was_reversed \
            else str(was_reversed)
        print(f"  {arm_id + '.' + role:16} {seen:5} {want:6} {flag:>14}")
        changes.setdefault(arm_id, {})[role] = {
            "spin": seen, "reversed": now_reversed,
        }

    flips = [
        f"{a}.{r}" for a, r, seen, want, _ in observations if seen != want
    ]
    print("=" * 70)
    if flips:
        print(f"{len(flips)} motor(s) turning the wrong way: {', '.join(flips)}")
    else:
        print("Every motor already turns the way the frame wants.")

    if ask("\nWrite these to vector.json?", {"y": "yes", "n": "no", "": "no"}) != "yes":
        print("Nothing written.")
        return 0

    with open(CONFIG_PATH) as handle:
        doc = json.load(handle)
    for entry in doc["arms"]:
        for role, values in changes.get(entry["id"], {}).items():
            entry["motors"][role].update(values)
    with open(CONFIG_PATH, "w") as handle:
        json.dump(doc, handle, indent=2)
        handle.write("\n")
    print(f"Written to {os.path.relpath(CONFIG_PATH)}")

    if flips:
        print("\nSERVO_BLH_RVMASK is @RebootRequired, and the HAL only ORs into its")
        print("reversed mask, so these do not take effect yet. Re-apply the output")
        print("mapping from the dashboard, then reboot the flight controller.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
