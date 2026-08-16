#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Snapshot shaping: turns bench and link state into the JSON the browser consumes.

Keys are camelCase because they land straight in TypeScript interfaces. Nothing in
here has side effects, so the broadcast loop can call it from a worker thread.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from . import kinematics as kin
from .bench import Bench
from .controller import LevelController


def _round(value: Any, digits: int = 2) -> Any:
    return None if value is None else round(float(value), digits)


def motor_percent(pwm: int, low: int = 1000, high: int = 2000) -> float:
    if high <= low:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (pwm - low) / (high - low)))


def arm_payload(bench: Bench, telemetry: Any) -> List[Dict[str, Any]]:
    """One entry per configured arm, live or not, so the UI can show what is planned."""
    entries: List[Dict[str, Any]] = []
    for arm in bench.config.arms:
        state = bench.arm_state(arm)
        thrust = kin.thrust_unit_vector(arm.mount_yaw_deg, state.tilt_outer, state.tilt_inner)

        axes = {}
        for name in ("outer", "inner"):
            axis = arm.gimbal.axis(name)
            pwm = state.outer_us if name == "outer" else state.inner_us
            axes[name] = {
                "channel": axis.channel,
                "pwm": pwm,
                "servoDeg": _round(axis.us_to_deg(pwm)),
                "tiltDeg": _round(state.tilt_outer if name == "outer" else state.tilt_inner),
                "limitDeg": arm.gimbal.tilt_limit_deg,
                "servoLimitDeg": axis.servo_limit_deg,
                "atLimit": state.outer_at_limit if name == "outer" else state.inner_at_limit,
            }

        motors = {}
        for role, motor in arm.motors.items():
            pwm = telemetry.servo_pwm.get(motor.channel, 1000) or 1000
            motors[role] = {
                "channel": motor.channel,
                "pwm": int(pwm),
                "percent": _round(motor_percent(int(pwm)), 1),
                "spin": motor.spin,
                "reversed": motor.reversed,
                "testSequence": motor.test_sequence,
                "rpm": _round(telemetry.esc_rpm.get(motor.channel), 0),
                "voltage": _round(telemetry.esc_voltage.get(motor.channel)),
                "current": _round(telemetry.esc_current.get(motor.channel)),
                "temperatureC": _round(telemetry.esc_temp.get(motor.channel), 0),
            }

        entries.append({
            "id": arm.id,
            "label": arm.label,
            "status": arm.status,
            "live": arm.live,
            "azimuthDeg": arm.azimuth_deg,
            "mountYawDeg": arm.mount_yaw_deg,
            "axes": axes,
            "motors": motors,
            "thrust": {
                "forwardDeg": _round(state.forward_deg),
                "rightDeg": _round(state.right_deg),
                "vector": [_round(v, 5) for v in thrust],
            },
        })
    return entries


def esc_payload(bench: Bench, telemetry: Any) -> List[Dict[str, Any]]:
    """Every ESC that is actually reporting, labelled with the motor it belongs to."""
    indices = sorted(
        set(telemetry.esc_voltage) | set(telemetry.esc_rpm) | set(telemetry.esc_temp)
    )
    out: List[Dict[str, Any]] = []
    for index in indices:
        owner = bench.config.arm_for_motor_channel(index)
        label = f"{owner[0].label} {owner[1]}" if owner else f"ESC {index}"
        out.append({
            "index": index,
            "label": label,
            "rpm": _round(telemetry.esc_rpm.get(index), 0),
            "voltage": _round(telemetry.esc_voltage.get(index)),
            "current": _round(telemetry.esc_current.get(index)),
            "temperatureC": _round(telemetry.esc_temp.get(index), 0),
        })
    return out


def controller_payload(controller: LevelController, telemetry: Any) -> Dict[str, Any]:
    status = controller.status()
    # What the law would ask for right now, whether or not it is running. Lets the UI
    # preview the command before anything is committed to the servos.
    preview_forward, preview_right, preview_saturated = controller.desired_lean(
        telemetry.roll_deg, telemetry.pitch_deg, telemetry.roll_rate_dps, telemetry.pitch_rate_dps
    )
    return {
        "active": status.active,
        "mode": status.mode,
        "levelGain": status.level_gain,
        "leadTimeS": status.lead_time_s,
        "maxTiltFraction": status.max_tilt_fraction,
        "invertRoll": status.invert_roll,
        "invertPitch": status.invert_pitch,
        "tiltCapDeg": status.tilt_cap_deg,
        "target": {
            "forward": status.target_forward_deg,
            "right": status.target_right_deg,
            "magnitude": status.target_magnitude_deg,
        },
        "preview": {
            "forward": _round(preview_forward),
            "right": _round(preview_right),
            "magnitude": _round((preview_forward ** 2 + preview_right ** 2) ** 0.5),
            "saturated": preview_saturated,
        },
        "saturated": status.saturated,
        "loopHz": status.loop_hz,
        "updates": status.updates,
        "lastError": status.last_error,
    }


def snapshot(bench: Bench, controller: LevelController) -> Dict[str, Any]:
    telemetry = bench.link.telemetry()
    budget = bench.link_budget()

    now = time.time()
    return {
        "link": {
            "connected": telemetry.connected,
            "device": telemetry.port,
            "baud": bench.config.link.baud,
            "systemId": telemetry.system_id,
            "componentId": telemetry.component_id,
            # None rather than a sentinel: "never seen" and "seen 999 s ago" are
            # different facts, and the UI renders them differently.
            "heartbeatAgeS": (
                None if not telemetry.connected else _round(telemetry.last_heartbeat_age_s)
            ),
            "readerAlive": bench.link.reader_alive,
            "updatedAt": telemetry.updated_at,
            "lastError": telemetry.error,
            "budget": {
                "channels": budget.channels,
                "rateHz": budget.rate_hz,
                "bytesPerSecond": round(budget.bytes_per_second),
                "capacityBytesPerSecond": round(budget.capacity_bytes_per_second),
                "utilisation": _round(budget.utilisation, 3),
                "overBudget": budget.over_budget,
            },
        },
        "vehicle": {
            "armed": telemetry.armed,
            "mode": telemetry.mode,
            "rollDeg": _round(telemetry.roll_deg, 1),
            "pitchDeg": _round(telemetry.pitch_deg, 1),
            "yawDeg": _round(telemetry.yaw_deg, 1),
            "rollRateDegS": _round(telemetry.roll_rate_dps, 1),
            "pitchRateDegS": _round(telemetry.pitch_rate_dps, 1),
            "yawRateDegS": _round(telemetry.yaw_rate_dps, 1),
            "rateMagnitudeDegS": _round(telemetry.rate_magnitude_dps, 1),
            "voltage": _round(telemetry.voltage_v),
            "current": _round(telemetry.current_a),
            "batteryRemaining": telemetry.battery_remaining,
            "loadPercent": _round(telemetry.load_pct, 1),
            "gpsFix": telemetry.gps_fix,
            "gpsSats": telemetry.gps_sats,
            "throttle": telemetry.throttle,
            "ageS": (
                None if not telemetry.updated_at else _round(now - telemetry.updated_at)
            ),
        },
        "arms": arm_payload(bench, telemetry),
        "escs": esc_payload(bench, telemetry),
        "controller": controller_payload(controller, telemetry),
        "outputs": {
            "owner": bench.outputs.owner,
            "rampActive": bench.outputs.ramp_active(),
            "motorTestActive": bench.motor_test_active,
        },
        # A flat, time-ordered list. The browser merges it with the commands it sent
        # itself, which only works if both sides carry real timestamps.
        "events": [
            {"id": event.seq, "at": event.at, "kind": event.kind, "text": event.text}
            for event in telemetry.events[-40:]
        ],
        "messageCounts": dict(sorted(telemetry.msg_counts.items())),
    }
